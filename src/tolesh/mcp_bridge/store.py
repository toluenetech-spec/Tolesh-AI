"""MCP server registry.

Modeled on the configuration surface LibreChat exposes
(``packages/data-provider/src/mcp.ts``): a named server has a transport
(``stdio`` or ``http``), either a command/args/env triple or a URL plus
headers, an enabled flag, a timeout and per-tool permission overrides.

Design rules:
  * the file lives outside the repository (``~/.config/tolesh``) and is 0600,
    because headers/env routinely carry a bearer token or a PAT;
  * reading a server through the API never returns those secrets — values are
    masked, and sending a masked value back is a no-op rather than a way to
    overwrite the real one with asterisks;
  * a server name is validated, because it becomes part of a tool id that the
    model sees and that the API routes on.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,39}$")
_TRANSPORTS = {"stdio", "http"}
_POLICIES = {"allow", "ask", "deny"}
MASK = "***"


def default_config_path() -> Path:
    override = os.environ.get("TOLESH_MCP_PATH")
    if override:
        return Path(override).expanduser()
    base = os.environ.get("TOLESH_CONFIG_DIR")
    root = Path(base).expanduser() if base else Path.home() / ".config" / "tolesh"
    return root / "mcp_servers.json"


def mask_secret(value: str) -> str:
    """Show enough to recognise a credential, never enough to use it."""
    if not value:
        return ""
    if len(value) <= 8:
        return MASK
    return f"{value[:3]}{MASK}{value[-2:]}"


def _mask_map(values: dict[str, Any]) -> dict[str, str]:
    return {k: mask_secret(str(v)) for k, v in values.items()}


def _merge_secrets(new: dict[str, Any], old: dict[str, Any]) -> dict[str, str]:
    """Keep the stored secret when the client sends back a masked preview."""
    merged: dict[str, str] = {}
    for key, value in new.items():
        text = "" if value is None else str(value)
        if MASK in text and key in old:
            merged[key] = str(old[key])
        else:
            merged[key] = text
    return merged


class MCPServerStore:
    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_config_path()

    # ------------------------------------------------------------------ io
    def _read(self) -> dict[str, Any]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and isinstance(raw.get("servers"), dict):
                return raw
        except Exception:
            pass
        return {"servers": {}}

    def _write(self, raw: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n",
                       encoding="utf-8")
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    # -------------------------------------------------------------- public
    def list(self, *, redact: bool = True) -> list[dict[str, Any]]:
        servers = self._read()["servers"]
        out = [self._public(name, cfg, redact=redact) for name, cfg in sorted(servers.items())]
        return out

    def get(self, name: str, *, redact: bool = True) -> dict[str, Any] | None:
        cfg = self._read()["servers"].get(name)
        if cfg is None:
            return None
        return self._public(name, cfg, redact=redact)

    def raw(self, name: str) -> dict[str, Any] | None:
        """Full configuration including secrets — for the transport only."""
        cfg = self._read()["servers"].get(name)
        return dict(cfg) if cfg else None

    def upsert(self, name: str, patch: dict[str, Any]) -> dict[str, Any]:
        if not _NAME_RE.match(name or ""):
            raise ValueError(
                "Server names must be 1-40 characters of letters, digits, '.', '_' or '-'.")
        raw = self._read()
        existing = raw["servers"].get(name, {})
        cfg = {**existing, **{k: v for k, v in patch.items() if v is not None}}
        cfg["name"] = name
        cfg["transport"] = str(cfg.get("transport") or "stdio").lower()
        if cfg["transport"] not in _TRANSPORTS:
            raise ValueError(f"Unsupported transport: {cfg['transport']}. "
                             "Use 'stdio' or 'http'.")
        if cfg["transport"] == "stdio":
            if not str(cfg.get("command") or "").strip():
                raise ValueError("A stdio server needs a command.")
            cfg["args"] = [str(a) for a in (cfg.get("args") or [])]
            cfg["env"] = _merge_secrets(dict(cfg.get("env") or {}),
                                        dict(existing.get("env") or {}))
        else:
            url = str(cfg.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                raise ValueError("An http server needs an http(s) URL.")
            cfg["url"] = url
            cfg["headers"] = _merge_secrets(dict(cfg.get("headers") or {}),
                                            dict(existing.get("headers") or {}))
        policy = str(cfg.get("default_policy") or "allow").lower()
        if policy not in _POLICIES:
            raise ValueError("default_policy must be allow, ask or deny.")
        cfg["default_policy"] = policy
        perms = cfg.get("tool_permissions") or {}
        if not isinstance(perms, dict):
            raise ValueError("tool_permissions must map tool name to allow/ask/deny.")
        cleaned: dict[str, str] = {}
        for tool, level in perms.items():
            if str(level).lower() not in _POLICIES:
                raise ValueError(f"Permission for {tool!r} must be allow, ask or deny.")
            cleaned[str(tool)] = str(level).lower()
        cfg["tool_permissions"] = cleaned
        cfg["enabled"] = bool(cfg.get("enabled", True))
        try:
            cfg["timeout_sec"] = max(1.0, min(300.0, float(cfg.get("timeout_sec") or 30)))
        except (TypeError, ValueError):
            raise ValueError("timeout_sec must be a number of seconds.") from None
        cfg["notes"] = str(cfg.get("notes") or "")[:1000]
        raw["servers"][name] = cfg
        self._write(raw)
        return self._public(name, cfg, redact=True)

    def delete(self, name: str) -> bool:
        raw = self._read()
        if name not in raw["servers"]:
            return False
        del raw["servers"][name]
        self._write(raw)
        return True

    def set_enabled(self, name: str, enabled: bool) -> dict[str, Any]:
        raw = self._read()
        if name not in raw["servers"]:
            raise KeyError(name)
        raw["servers"][name]["enabled"] = bool(enabled)
        self._write(raw)
        return self._public(name, raw["servers"][name], redact=True)

    # ------------------------------------------------------------- helpers
    @staticmethod
    def policy_for(cfg: dict[str, Any], tool: str) -> str:
        per_tool = cfg.get("tool_permissions") or {}
        return str(per_tool.get(tool) or cfg.get("default_policy") or "allow").lower()

    @staticmethod
    def _public(name: str, cfg: dict[str, Any], *, redact: bool) -> dict[str, Any]:
        out = {
            "name": name,
            "transport": cfg.get("transport", "stdio"),
            "enabled": bool(cfg.get("enabled", True)),
            "timeout_sec": cfg.get("timeout_sec", 30),
            "default_policy": cfg.get("default_policy", "allow"),
            "tool_permissions": dict(cfg.get("tool_permissions") or {}),
            "notes": cfg.get("notes", ""),
            "configured": bool(cfg.get("command") or cfg.get("url")),
        }
        if cfg.get("transport") == "stdio":
            out["command"] = cfg.get("command", "")
            out["args"] = list(cfg.get("args") or [])
            out["env"] = (_mask_map(cfg.get("env") or {}) if redact
                          else dict(cfg.get("env") or {}))
            out["url"] = ""
            out["headers"] = {}
        else:
            out["url"] = cfg.get("url", "")
            out["headers"] = (_mask_map(cfg.get("headers") or {}) if redact
                              else dict(cfg.get("headers") or {}))
            out["command"] = ""
            out["args"] = []
            out["env"] = {}
        return out
