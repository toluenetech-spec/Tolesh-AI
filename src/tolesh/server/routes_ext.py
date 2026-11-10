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
    AgentRunCreate, ConnectorActionRun, ConnectorOAuthComplete,
    ConnectorOAuthStart, ConnectorTokenCreate, MCPInvoke, MCPPromptRun,
    MCPServerPatch,
)


def register_extension_routes(app: FastAPI, st: Any) -> None:
    _register_mcp(app, st)
    _register_connectors(app, st)
    _register_agents(app, st)

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


# -------------------------------------------------------------- connectors
def _register_connectors(app: FastAPI, st: Any) -> None:
    from fastapi.responses import HTMLResponse

    from ..connectors import ConnectorError

    @app.get("/api/connectors")
    def list_connectors() -> dict[str, Any]:
        """The provider catalogue plus every connected account.

        Both halves matter: the UI needs to show what *can* be connected even
        when nothing is, and an empty list alone cannot say that.
        """
        return {"providers": st.connectors.catalogue(),
                "connections": st.connectors.connections()}

    @app.post("/api/connectors/token")
    def connect_with_token(body: ConnectorTokenCreate) -> dict[str, Any]:
        try:
            connection = st.connectors.add_token(
                body.provider, body.token, label=body.label, base_url=body.base_url)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"connection": connection}

    @app.post("/api/connectors/oauth/start")
    def oauth_start(body: ConnectorOAuthStart) -> dict[str, Any]:
        try:
            return st.connectors.begin_oauth(
                body.provider, client_id=body.client_id,
                client_secret=body.client_secret, redirect_uri=body.redirect_uri,
                scopes=body.scopes or None, base_url=body.base_url)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/api/connectors/oauth/callback")
    def oauth_callback(code: str = "", state: str = "", error: str = "",
                       error_description: str = "") -> Any:
        """The redirect target registered with the provider.

        This route is exempt from the API token guard because the provider's
        redirect cannot carry a bearer header. The single-use ``state`` value
        is the credential here, and it is consumed atomically; a callback
        without it does nothing.
        """
        if error:
            message = error_description or error
            return HTMLResponse(
                _callback_page(False, f"The provider refused the sign-in: {message}"),
                status_code=400)
        if not code or not state:
            return HTMLResponse(_callback_page(False, "The callback was incomplete."),
                                status_code=400)
        try:
            connection = st.connectors.complete_oauth(state, code)
        except ConnectorError as exc:
            return HTMLResponse(_callback_page(False, str(exc)), status_code=400)
        return HTMLResponse(_callback_page(True, f"Connected {connection.get('label', '')}."))

    @app.post("/api/connectors/oauth/complete")
    def oauth_complete(body: ConnectorOAuthComplete) -> dict[str, Any]:
        """For clients that capture the redirect themselves (desktop, mobile)."""
        try:
            return {"connection": st.connectors.complete_oauth(
                body.state, body.code, base_url=body.base_url)}
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/connectors/{connection_id}/test")
    def test_connection(connection_id: str) -> dict[str, Any]:
        try:
            return st.connectors.test(connection_id)
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/connectors/{connection_id}")
    def delete_connection(connection_id: str) -> dict[str, Any]:
        if not st.connectors.delete(connection_id):
            raise HTTPException(status_code=404, detail="Unknown connection.")
        return {"status": "ok"}

    @app.get("/api/connectors/{connection_id}/actions")
    def connector_actions(connection_id: str) -> dict[str, Any]:
        try:
            return {"actions": st.connectors.actions(connection_id)}
        except ConnectorError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/connectors/{connection_id}/actions/{action}")
    def run_connector_action(connection_id: str, action: str,
                             body: ConnectorActionRun) -> dict[str, Any]:
        """Run one action, parking writes behind the approval gate."""
        connection = st.connectors.store.get(connection_id)
        if connection is None:
            raise HTTPException(status_code=404, detail="Unknown connection.")
        actions = {a["name"]: a for a in st.connectors.actions(connection_id)}
        if action not in actions:
            raise HTTPException(status_code=404, detail=f"No action called {action}.")
        needs_approval = actions[action]["requires_approval"]
        if needs_approval and not body.approval_id:
            decision = st.gate.request(
                f"connector:{connection['provider']}:{action}", body.args,
                reason=f"{connection['label']}: {action} changes data outside Tolesh.")
            return {"status": "pending-approval", "action": action,
                    "approval": decision}
        if needs_approval:
            _consume_approval(st, body.approval_id, body.approval_token or "")
        try:
            result = st.connectors.run_action(connection_id, action, body.args,
                                              approved=needs_approval)
        except ConnectorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"status": "ok", "result": result}


def _callback_page(ok: bool, message: str) -> str:
    colour = "#1f7a4d" if ok else "#a3341f"
    title = "Connected" if ok else "Not connected"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Tolesh — {title}</title>
<style>
body{{font-family:system-ui,sans-serif;background:#f7f4ef;color:#22201d;
display:flex;align-items:center;justify-content:center;height:100vh;margin:0}}
.card{{max-width:32rem;padding:2rem 2.25rem;background:#fff;border-radius:14px;
box-shadow:0 10px 30px rgba(0,0,0,.08);border-top:4px solid {colour}}}
h1{{font-size:1.15rem;margin:0 0 .6rem;color:{colour}}}
p{{margin:0;line-height:1.5;color:#4a453f}}
</style></head><body><div class="card"><h1>{title}</h1>
<p>{message}</p><p style="margin-top:1rem">You can close this tab and return to Tolesh.</p>
</div></body></html>"""


# ------------------------------------------------------------------ agents
def _register_agents(app: FastAPI, st: Any) -> None:
    from fastapi.responses import StreamingResponse

    from ..agents import build_registry

    @app.get("/api/agents/tools")
    def agent_tools() -> dict[str, Any]:
        """The catalogue the loop will see, with risk and source attached."""
        registry = build_registry(st.agent_context)
        return {"tools": [t.as_dict() for t in registry.list()],
                "count": len(registry.list())}

    @app.post("/api/agents/runs")
    def create_run(body: AgentRunCreate) -> dict[str, Any]:
        try:
            run = st.agents.create(body.goal, model=body.model,
                                   conversation_id=body.conversation_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"run": run}

    @app.get("/api/agents/runs")
    def list_runs(limit: int = 30) -> dict[str, Any]:
        return {"runs": st.agent_history.list(limit=max(1, min(limit, 100)))}

    @app.get("/api/agents/runs/{run_id}")
    def get_run(run_id: str) -> dict[str, Any]:
        run = st.agent_history.get(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Unknown run.")
        return {"run": run, "messages": st.agent_history.messages(run_id)}

    @app.delete("/api/agents/runs/{run_id}")
    def delete_run(run_id: str) -> dict[str, Any]:
        if not st.agent_history.delete(run_id):
            raise HTTPException(status_code=404, detail="Unknown run.")
        return {"status": "ok"}

    @app.post("/api/agents/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict[str, Any]:
        if st.agent_history.get(run_id) is None:
            raise HTTPException(status_code=404, detail="Unknown run.")
        return st.agents.cancel(run_id)

    def _stream(run_id: str) -> StreamingResponse:
        if st.agent_history.get(run_id) is None:
            raise HTTPException(status_code=404, detail="Unknown run.")
        # The router can be swapped (tests, model changes); the runner must use
        # the one this server is actually routing with.
        st.agents.router = st.router

        def gen():
            for event in st.agents.stream(run_id, st.agent_context):
                yield _sse(event.as_dict())

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.post("/api/agents/runs/{run_id}/run")
    def run_agent(run_id: str) -> StreamingResponse:
        return _stream(run_id)

    @app.post("/api/agents/runs/{run_id}/resume")
    def resume_agent(run_id: str) -> StreamingResponse:
        """Continue a run that parked on an approval.

        The approval itself is granted through ``/api/permissions/{id}/approve``;
        this endpoint only refuses if the approval is still missing.
        """
        return _stream(run_id)


def _sse(payload: dict[str, Any]) -> str:
    import json as _json

    return "data: " + _json.dumps(payload, ensure_ascii=False) + "\n\n"
