"""Safe, permission-gated computer interaction.

Every invocation passes through :class:`permissions.gate.PermissionGate`.
Read-only actions run immediately (and are audited). Elevated/consequential
actions return a pending confirmation request instead of executing; the UI
shows the user exactly what would happen and must approve with the token.

Deliberately NOT implemented even after approval: delete_file, run_command,
shutdown, restart, send_message, network_request. The brief requires safety
controls *before* consequential actions; shipping those without a hardened,
audited executor would be dishonest. They return an explicit refusal.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from ..permissions import PermissionGate, RiskLevel, TOOL_POLICY, app_command, classify, is_path_allowed
from ..system import ControlError, SystemControls

_NOT_IMPLEMENTED = {
    "delete_file", "run_command", "shutdown", "restart",
    "send_message", "network_request",
}


class ComputerTools:
    def __init__(self, gate: PermissionGate | None = None,
                 controls: SystemControls | None = None):
        self.gate = gate or PermissionGate()
        # Injectable so a test can drive every platform branch, and so the
        # controls report what this machine can do rather than what it should.
        self.controls = controls or SystemControls()

    def list_tools(self) -> list[dict[str, Any]]:
        capabilities = self.controls.capabilities()["controls"]
        out = []
        for name, level in TOOL_POLICY.items():
            entry = {"name": name, "risk": level.value,
                     "implemented": name not in _NOT_IMPLEMENTED}
            if name in capabilities:
                # A control that cannot run here says so up front, instead of
                # failing after the user has already approved it.
                entry["available"] = capabilities[name]["supported"]
                entry["detail"] = capabilities[name]["detail"]
            out.append(entry)
        return out

    def invoke(self, tool: str, args: dict[str, Any] | None = None,
               *, reason: str = "", approved: str | dict[str, Any] | None = None) -> dict[str, Any]:
        """Request an action, or run one that a human already approved.

        ``approved`` is an *identifier*, never a decision. A caller that could
        pass ``{"status": "approved"}`` would be able to approve itself, and a
        caller that could pass its own args alongside an approval could approve
        a volume change of 10 and then run one of 100. So the gate's own stored
        row is the only source of truth here: the id is looked up, and the tool
        and argument set must match what was actually approved.
        """
        args = args or {}
        if approved is None:
            decision = self.gate.request(tool, args, reason=reason)
            if decision["status"] != "auto-approved":
                # Keep our own status key last so it is not overwritten by the
                # gate's "pending" value (dict merge order matters here).
                return {**decision, "status": "pending-confirmation"}
        else:
            action_id = approved if isinstance(approved, str) else str(
                approved.get("id") or "")
            decision = self._approved_record(action_id, tool, args)
            if decision is None:
                return {"status": "refused", "tool": tool,
                        "message": "No approval matches this exact action."}
        return self.execute(tool, args, risk=decision.get("risk", classify(tool, args).value))

    def _approved_record(self, action_id: str, tool: str,
                         args: dict[str, Any]) -> dict[str, Any] | None:
        """The stored approval, or ``None`` if it does not cover this call."""
        if not action_id:
            return None
        record = self.gate.status_of(action_id)
        if record.get("status") != "approved":
            return None
        if record.get("tool") != tool:
            return None
        if _canonical(record.get("args") or {}) != _canonical(args):
            return None
        return record

    def execute(self, tool: str, args: dict[str, Any] | None = None,
                *, risk: str = "safe") -> dict[str, Any]:
        args = args or {}
        if tool in _NOT_IMPLEMENTED:
            return {"status": "refused", "tool": tool,
                    "message": f"'{tool}' is not implemented. Tolesh will not perform this consequential action without a hardened, audited executor."}
        if tool == "get_time":
            return {"status": "ok", "tool": tool,
                    "result": datetime.now().astimezone().isoformat(timespec="seconds")}
        if tool == "system_info":
            return {"status": "ok", "tool": tool, "result": {
                "platform": platform.system(), "release": platform.release(),
                "machine": platform.machine(), "python": platform.python_version(),
                "cpu_count": os.cpu_count(), "home": str(Path.home()),
            }}
        if tool == "list_directory":
            raw = args.get("path", str(Path.home()))
            if not is_path_allowed(str(raw)):
                return {"status": "refused", "tool": tool, "message": "Path outside the allowed sandbox."}
            p = Path(raw).expanduser()
            if not p.is_dir():
                return {"status": "error", "tool": tool, "message": "Not a directory."}
            entries = []
            for child in sorted(p.iterdir())[:200]:
                entries.append({"name": child.name, "type": "dir" if child.is_dir() else "file",
                                "size": child.stat().st_size if child.is_file() else None})
            return {"status": "ok", "tool": tool, "result": {"path": str(p), "entries": entries}}
        if tool == "read_text_file":
            raw = args.get("path", "")
            if not raw or not is_path_allowed(str(raw)):
                return {"status": "refused", "tool": tool, "message": "Path outside the allowed sandbox."}
            p = Path(raw).expanduser()
            if not p.is_file():
                return {"status": "error", "tool": tool, "message": "Not a file."}
            if p.stat().st_size > 200_000:
                return {"status": "refused", "tool": tool, "message": "File exceeds the 200 KB read limit."}
            try:
                return {"status": "ok", "tool": tool,
                        "result": {"path": str(p), "text": p.read_text(encoding="utf-8", errors="replace")}}
            except Exception as exc:
                return {"status": "error", "tool": tool, "message": str(exc)}
        if tool == "write_text_file":
            raw = args.get("path", "")
            if not raw or not is_path_allowed(str(raw)):
                return {"status": "refused", "tool": tool, "message": "Path outside the allowed sandbox."}
            p = Path(raw).expanduser()
            try:
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(str(args.get("content", "")), encoding="utf-8")
                return {"status": "ok", "tool": tool, "result": {"path": str(p), "bytes": p.stat().st_size}}
            except Exception as exc:
                return {"status": "error", "tool": tool, "message": str(exc)}
        if tool == "open_application":
            name = str(args.get("name", ""))
            candidates = app_command(name)
            if not candidates:
                return {"status": "refused", "tool": tool, "message": "Application is not in the allowlist."}
            exe = next((c for c in candidates if shutil.which(c)), None)
            if exe is None:
                return {"status": "error", "tool": tool,
                        "message": "No launcher found for this application in the current environment."}
            try:
                subprocess.Popen([exe], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return {"status": "ok", "tool": tool, "result": {"launched": exe}}
            except Exception as exc:
                return {"status": "error", "tool": tool, "message": str(exc)}
        if tool in ("set_volume", "set_brightness"):
            try:
                result = (self.controls.set_volume(args.get("level"))
                          if tool == "set_volume"
                          else self.controls.set_brightness(args.get("level")))
            except ControlError as exc:
                return _control_failure(tool, exc)
            return result
        if tool == "lock_screen":
            try:
                return self.controls.lock_screen()
            except ControlError as exc:
                return _control_failure(tool, exc)
        return {"status": "error", "tool": tool, "message": "Unknown tool."}


def _control_failure(tool: str, exc: ControlError) -> dict[str, Any]:
    """A control that could not run is reported, never reported as done."""
    status = {"unsupported": "unsupported", "missing": "unavailable"}.get(
        exc.kind, "error")
    return {"status": status, "tool": tool, "message": str(exc),
            "kind": exc.kind, "utility": exc.utility}


def _canonical(args: dict[str, Any]) -> str:
    """Stable rendering of an argument set, for exact comparison.

    ``{"level": 30}`` and ``{"level": "30"}`` are different strings on purpose:
    the approval named one call, and a control that reads its own integer is
    not a licence to accept a differently-typed one.
    """
    import json

    return json.dumps(args, sort_keys=True, default=str, ensure_ascii=False)

