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

_NOT_IMPLEMENTED = {
    "delete_file", "run_command", "shutdown", "restart",
    "send_message", "network_request",
}


class ComputerTools:
    def __init__(self, gate: PermissionGate | None = None):
        self.gate = gate or PermissionGate()

    def list_tools(self) -> list[dict[str, Any]]:
        return [
            {"name": name, "risk": level.value,
             "implemented": name not in _NOT_IMPLEMENTED}
            for name, level in TOOL_POLICY.items()
        ]

    def invoke(self, tool: str, args: dict[str, Any] | None = None,
               *, reason: str = "", approved: dict[str, Any] | None = None) -> dict[str, Any]:
        args = args or {}
        if approved is None:
            decision = self.gate.request(tool, args, reason=reason)
            if decision["status"] != "auto-approved":
                # Keep our own status key last so it is not overwritten by the
                # gate's "pending" value (dict merge order matters here).
                return {**decision, "status": "pending-confirmation"}
        else:
            decision = approved
        return self.execute(tool, args, risk=decision.get("risk", classify(tool, args).value))

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
        return {"status": "error", "tool": tool, "message": "Unknown tool."}
