"""Which computer actions are safe, elevated, or consequential.

Ported conceptually from Mark-LV's ``core/confirm.py`` (where destructive
settings changes required a human CONFIRM token) and generalised into a policy
table. Consequential actions are NEVER executed without an explicit approval.
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path


class RiskLevel(str, Enum):
    SAFE = "safe"                    # read-only, no side effects
    ELEVATED = "elevated"            # launches something, writes a file
    CONSEQUENTIAL = "consequential"  # deletes, executes, sends, network, shutdown


TOOL_POLICY: dict[str, RiskLevel] = {
    "get_time": RiskLevel.SAFE,
    "system_info": RiskLevel.SAFE,
    "list_directory": RiskLevel.SAFE,
    "read_text_file": RiskLevel.SAFE,
    "open_application": RiskLevel.ELEVATED,
    "write_text_file": RiskLevel.ELEVATED,
    # Three narrow system controls. Elevated, not consequential: each changes a
    # setting the user can see and change back, and each still needs an explicit
    # confirmation token. Shutdown and restart stay consequential — and refused.
    "set_volume": RiskLevel.ELEVATED,
    "set_brightness": RiskLevel.ELEVATED,
    "lock_screen": RiskLevel.ELEVATED,
    "delete_file": RiskLevel.CONSEQUENTIAL,
    "run_command": RiskLevel.CONSEQUENTIAL,
    "shutdown": RiskLevel.CONSEQUENTIAL,
    "restart": RiskLevel.CONSEQUENTIAL,
    "send_message": RiskLevel.CONSEQUENTIAL,
    "network_request": RiskLevel.CONSEQUENTIAL,
}

# Only well-known, non-destructive applications may be launched by name.
ALLOWED_APPS = {
    "file-manager", "text-editor", "terminal", "browser", "calculator",
    "code-editor", "music-player",
}

_APP_COMMANDS = {
    "file-manager": ("xdg-open", "explorer", "open"),
    "text-editor": ("gedit", "notepad", "open"),
    "terminal": ("x-terminal-emulator", "gnome-terminal", "cmd", "open"),
    "browser": ("xdg-open", "start", "open"),
    "calculator": ("gnome-calculator", "calc", "open"),
    "code-editor": ("code", "open"),
    "music-player": ("rhythmbox", "open"),
}


def app_command(name: str) -> tuple[str, ...]:
    return _APP_COMMANDS.get(name, ())


def classify(tool: str, args: dict | None = None) -> RiskLevel:
    level = TOOL_POLICY.get(tool, RiskLevel.CONSEQUENTIAL)
    # An explicitly unknown tool is treated as consequential, never as safe.
    args = args or {}
    if tool == "open_application" and args.get("name") not in ALLOWED_APPS:
        return RiskLevel.CONSEQUENTIAL
    return level


def _sandbox_roots() -> list[Path]:
    roots = [Path.home()]
    override = os.environ.get("TOLESH_SANDBOX_ROOT")
    if override:
        roots.append(Path(override).expanduser())
    return roots


def is_path_allowed(raw: str) -> bool:
    """Path boundary check. Refuses traversal outside the user's home (or an
    explicitly configured sandbox root) and refuses sensitive dot-directories."""
    try:
        p = Path(raw).expanduser().resolve()
    except Exception:
        return False
    roots = [r.resolve() for r in _sandbox_roots()]
    if not any(p == r or r in p.parents for r in roots):
        return False
    blocked = {".ssh", ".gnupg", ".aws", ".config/tolesh", ".local/share/tolesh"}
    for parent in p.parents:
        if any(str(parent).endswith(b) for b in blocked):
            return False
    return True
