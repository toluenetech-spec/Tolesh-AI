"""API routes for the capability layers added after the initial release.

Kept out of ``app.py`` so the core chat surface stays readable. Each phase
registers its own block through :func:`register_extension_routes`; a failure
in one block (a missing optional dependency, say) must not take the others
down, so each is guarded and reports its own availability in
``/api/capabilities``.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException

from .schemas import (
    MCPInvoke, MCPPromptRun, MCPServerPatch,
)


def register_extension_routes(app: FastAPI, st: Any) -> None:
    _register_mcp(app, st)

    @app.get("/api/capabilities")
    def capabilities() -> dict[str, Any]:
        """What this build can actually do, and what is blocked.

        The UI reads this instead of assuming a feature exists: a switch that
        silently does nothing is worse than a switch that explains itself.
        """
        mcp_status = []
        try:
            mcp_status = st.mcp.status()
        except Exception as exc:  # pragma: no cover - defensive
            mcp_status = [{"error": str(exc)}]
        return {
            "phases": {
                "mcp": {"available": True, "servers": len(mcp_status)},
                "connectors": {"available": hasattr(st, "connectors")},
                "agents": {"available": hasattr(st, "agents")},
                "artifacts": {"available": hasattr(st, "artifacts")},
                "jobs": {"available": hasattr(st, "jobs")},
                "media": {"available": hasattr(st, "media")},
                "assistants": {"available": hasattr(st, "assistants")},
            },
            "mcp_servers": mcp_status,
            "time": time.time(),
        }


# --------------------------------------------------------------------- MCP
def _register_mcp(app: FastAPI, st: Any) -> None:
    @app.get("/api/mcp/servers")
    def list_servers() -> dict[str, Any]:
        """Configured servers, with secrets masked and live state attached."""
        servers = st.mcp.store.list(redact=True)
        status = {row["server"]: row for row in st.mcp.status()}
        for server in servers:
            server["status"] = status.get(server["name"], {})
        return {"servers": servers}

    @app.post("/api/mcp/servers")
    def create_server(body: MCPServerPatch) -> dict[str, Any]:
        if not body.name:
            raise HTTPException(status_code=400, detail="A server name is required.")
        try:
            server = st.mcp.store.upsert(body.name, body.model_dump(exclude_none=True))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"server": server}

    @app.patch("/api/mcp/servers/{name}")
    def update_server(name: str, body: MCPServerPatch) -> dict[str, Any]:
        if st.mcp.store.get(name, redact=False) is None:
            raise HTTPException(status_code=404, detail="Unknown MCP server.")
        patch = body.model_dump(exclude_none=True)
        patch.pop("name", None)
        try:
            server = st.mcp.store.upsert(name, patch)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        # A changed configuration invalidates the live session.
        st.mcp.disconnect(name)
        return {"server": server}

    @app.delete("/api/mcp/servers/{name}")
    def delete_server(name: str) -> dict[str, Any]:
        st.mcp.disconnect(name)
        if not st.mcp.store.delete(name):
            raise HTTPException(status_code=404, detail="Unknown MCP server.")
        return {"status": "ok"}

    @app.post("/api/mcp/servers/{name}/test")
    def test_server(name: str) -> dict[str, Any]:
        """Connect for real and report what the server answered."""
        result = st.mcp.test(name)
        return result

    @app.post("/api/mcp/servers/{name}/connect")
    def connect_server(name: str) -> dict[str, Any]:
        try:
            return st.mcp.connect(name, force=True)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/mcp/servers/{name}/disconnect")
    def disconnect_server(name: str) -> dict[str, Any]:
        return st.mcp.disconnect(name)

    @app.get("/api/mcp/servers/{name}/tools")
    def server_tools(name: str) -> dict[str, Any]:
        try:
            info = st.mcp.connect(name)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"server": name, "tools": info["tools"], "resources": info["resources"],
                "prompts": info["prompts"], "server_info": info["server_info"]}

    @app.get("/api/mcp/servers/{name}/resources")
    def server_resources(name: str) -> dict[str, Any]:
        try:
            return {"server": name, "resources": st.mcp.resources(name)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/mcp/servers/{name}/resources/read")
    def read_resource(name: str, body: dict[str, str]) -> dict[str, Any]:
        uri = (body or {}).get("uri", "")
        if not uri:
            raise HTTPException(status_code=400, detail="A resource uri is required.")
        try:
            return st.mcp.read_resource(name, uri)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/mcp/servers/{name}/prompts")
    def server_prompts(name: str) -> dict[str, Any]:
        try:
            return {"server": name, "prompts": st.mcp.prompts(name)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/api/mcp/servers/{name}/prompts/run")
    def run_prompt(name: str, body: MCPPromptRun) -> dict[str, Any]:
        try:
            return st.mcp.get_prompt(name, body.prompt, body.arguments)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.get("/api/mcp/tools")
    def all_tools() -> dict[str, Any]:
        """Every tool on every reachable server, qualified for the model."""
        tools = st.mcp.all_tools()
        return {"tools": tools, "count": len(tools)}

    @app.post("/api/mcp/invoke")
    def invoke(body: MCPInvoke) -> dict[str, Any]:
        approval = None
        if body.approval_id:
            approval = _consume_approval(st, body.approval_id, body.approval_token)
        result = st.mcp.invoke(body.server, body.tool, body.args,
                               approval=approval, reason=body.reason)
        if result.get("status") == "pending-approval":
            return result
        return result

    @app.get("/api/mcp/calls")
    def calls(limit: int = 50) -> dict[str, Any]:
        return {"calls": st.mcp.calls(limit=max(1, min(limit, 200)))}


def _consume_approval(st: Any, action_id: str, token: str) -> dict[str, Any]:
    """Return an approved decision, approving it only if it is still pending.

    The token check lives here because the gate owns it. An action already
    approved through ``/api/permissions/{id}/approve`` must stay usable: the
    approval and the tool call are two requests, and re-approving an approved
    action would fail and block a call the human already allowed.
    """
    state = st.gate.status_of(action_id)
    if state.get("status") == "unknown":
        raise HTTPException(status_code=400, detail="Unknown approval id.")
    if state.get("status") == "approved":
        return {"status": "approved", **state}
    if state.get("status") != "pending":
        raise HTTPException(status_code=400,
                            detail=f"The approval is {state.get('status')}.")
    decision = st.gate.approve(action_id, token or "")
    if decision.get("status") == "error":
        raise HTTPException(status_code=400, detail=decision.get("message", "Approval failed."))
    return decision
