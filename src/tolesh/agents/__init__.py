"""Multi-step agents: a bounded loop over a real tool catalogue."""

from __future__ import annotations

from typing import Any

from .history import AgentHistory
from .loop import AgentEvent, AgentRunner, parse_reply
from .tools import ToolRegistry, ToolResult, ToolSpec, build_registry


class WorkspaceContext:
    """Everything a tool may touch, passed explicitly instead of imported.

    Explicit wiring keeps the tool layer testable: a test can hand the loop a
    context with one fake connector and no MCP manager at all.
    """

    def __init__(self, **parts: Any):
        for key, value in parts.items():
            setattr(self, key, value)

    def has(self, name: str) -> bool:
        return getattr(self, name, None) is not None


__all__ = [
    "AgentEvent", "AgentHistory", "AgentRunner", "ToolRegistry", "ToolResult",
    "ToolSpec", "WorkspaceContext", "build_registry", "parse_reply",
]
