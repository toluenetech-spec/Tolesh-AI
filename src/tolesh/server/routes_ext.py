"""API routes for the capability layers added after the initial release.

Kept out of ``app.py`` so the core chat surface stays readable. Each phase
registers its own block through :func:`register_extension_routes`; a failure
in one block (a missing optional dependency, say) must not take the others
down, so each is guarded and reports its own availability in
``/api/capabilities``.
"""

from __future__ import annotations

import sys
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from . import media_urls

from .schemas import (
    AgentRunCreate, ArtifactAnalyse, ArtifactChart, ConnectorActionRun,
    ConnectorOAuthComplete, ConnectorOAuthStart, ConnectorTokenCreate,
    AssistantCreate, AssistantPatch, BranchCreate, DataDelete, JobCreate,
    JobPatch, MCPInvoke, MCPPromptRun, MCPServerPatch, MediaDescribe,
    MediaGenerate, MediaSpeak, MessageEdit, ShareCreate, TelegramPatch,
    TemplateCreate,
)


def register_extension_routes(app: FastAPI, st: Any) -> None:
    _register_mcp(app, st)
    _register_connectors(app, st)
    _register_agents(app, st)
    _register_artifacts(app, st)
    _register_media(app, st)
    _register_jobs(app, st)
    _register_settings(app, st)
    _register_voice(app, st)
    _register_telegram(app, st)
    _register_assistants(app, st)
    _register_sharing(app, st)
    _register_editing(app, st)

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
                "telegram": {"available": hasattr(st, "telegram")},
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
        st.agent_context.router = st.router

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


# --------------------------------------------------------------- artifacts
def _register_artifacts(app: FastAPI, st: Any) -> None:
    from fastapi.responses import FileResponse

    from ..artifacts import AnalysisError, chart_png, summarise, summarise_text

    def _signed(artifact: dict[str, Any]) -> dict[str, Any]:
        """Attach a download URL the browser can use without the bearer token.

        The store builds a bare path; an ``<img src>`` cannot send headers, so
        every preview would 401 as soon as the API is token-guarded.
        """
        return media_urls.with_signed_url(st.media_secret, artifact)

    @app.get("/api/artifacts")
    def list_artifacts(conversation_id: str | None = None,
                       kind: str | None = None) -> dict[str, Any]:
        return {"artifacts": [_signed(item) for item in
                              st.artifacts.list(conversation_id=conversation_id,
                                                kind=kind)],
                "stats": st.artifacts.stats()}

    @app.get("/api/artifacts/{artifact_id}")
    def get_artifact(artifact_id: str) -> dict[str, Any]:
        meta = st.artifacts.get(artifact_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Unknown artifact.")
        return {"artifact": _signed(meta)}

    @app.get("/api/artifacts/{artifact_id}/download")
    def download_artifact(artifact_id: str) -> Any:
        """Serve the file itself.

        ``FileResponse`` sets the filename from the stored, sanitised name, so a
        crafted name cannot inject a header or escape the artifact directory.
        """
        meta = st.artifacts.get(artifact_id)
        path = st.artifacts.path(artifact_id)
        if meta is None or path is None:
            raise HTTPException(status_code=404, detail="Unknown artifact.")
        return FileResponse(str(path), media_type=meta["mime"] or "application/octet-stream",
                            filename=meta["name"])

    @app.delete("/api/artifacts/{artifact_id}")
    def delete_artifact(artifact_id: str) -> dict[str, Any]:
        if not st.artifacts.delete(artifact_id):
            raise HTTPException(status_code=404, detail="Unknown artifact.")
        return {"status": "ok"}

    @app.post("/api/artifacts/analyse")
    def analyse(body: ArtifactAnalyse) -> dict[str, Any]:
        """Profile an uploaded table. Real statistics, no model involved."""
        meta = st.attachments.get(body.attachment_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Unknown attachment.")
        data = st.attachments.read_bytes(body.attachment_id)
        if data is None:
            raise HTTPException(status_code=400, detail="That upload is no longer on disk.")
        try:
            profile = summarise(data, name=meta["filename"])
        except AnalysisError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"profile": profile, "summary": summarise_text(profile),
                "attachment": {"id": meta["id"], "filename": meta["filename"]}}

    @app.post("/api/artifacts/chart")
    def make_chart(body: ArtifactChart) -> dict[str, Any]:
        meta = st.attachments.get(body.attachment_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Unknown attachment.")
        data = st.attachments.read_bytes(body.attachment_id)
        if data is None:
            raise HTTPException(status_code=400, detail="That upload is no longer on disk.")
        try:
            png, chart_meta = chart_png(data, chart_type=body.chart_type,
                                        x=body.x, y=body.y, title=body.title,
                                        name=meta["filename"])
        except AnalysisError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        artifact = st.artifacts.save_bytes(
            f"{chart_meta['chart_type']}-{chart_meta['x']}.png", png,
            kind="image", mime="image/png", meta=chart_meta,
            conversation_id=body.conversation_id)
        return {"artifact": _signed(artifact)}


# ------------------------------------------------------------------- media
def _register_media(app: FastAPI, st: Any) -> None:
    from fastapi.responses import Response

    from ..media import (MediaError, SpeechError, capabilities, describe_image,
                         generate_image, synthesize, transcribe, wav_duration)

    @app.get("/api/media/capabilities")
    def media_capabilities() -> dict[str, Any]:
        """What this machine can really do, including why it cannot."""
        return capabilities()

    @app.post("/api/media/describe")
    def media_describe(body: MediaDescribe) -> dict[str, Any]:
        meta = st.attachments.get(body.attachment_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Unknown attachment.")
        data = st.attachments.read_bytes(body.attachment_id)
        if data is None:
            raise HTTPException(status_code=400, detail="That upload is no longer on disk.")
        if not (meta["mime"] or "").startswith("image/"):
            raise HTTPException(status_code=400,
                                detail="That file is not an image.")
        try:
            result = describe_image(st.router, data, prompt=body.prompt,
                                    model=body.model, mime=meta["mime"])
        except MediaError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"result": result, "attachment": {"id": meta["id"],
                                                 "filename": meta["filename"]}}

    @app.post("/api/media/images")
    def media_image(body: MediaGenerate) -> dict[str, Any]:
        try:
            image, meta = generate_image(body.prompt, size=body.size, model=body.model)
        except MediaError as exc:
            status = 503 if exc.kind == "unconfigured" else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        artifact = st.artifacts.save_bytes(
            f"image-{meta['model']}.png", image, kind="image", mime="image/png",
            meta=meta, conversation_id=body.conversation_id)
        return {"artifact": artifact}

    @app.post("/api/media/transcribe")
    async def media_transcribe(request: Request, language: str | None = None,
                               model_size: str = "base") -> dict[str, Any]:
        audio = await request.body()
        try:
            return transcribe(audio, language=language, model_size=model_size)
        except SpeechError as exc:
            status = 503 if exc.kind == "unavailable" else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.post("/api/media/speak")
    def media_speak(body: MediaSpeak) -> Any:
        try:
            audio, mime, meta = synthesize(body.text, voice=body.voice)
        except SpeechError as exc:
            status = 503 if exc.kind == "unconfigured" else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc
        headers = {"X-Tolesh-Voice-Engine": str(meta.get("engine", "")),
                   "X-Tolesh-Audio-Seconds": str(wav_duration(audio)),
                   "Content-Length": str(len(audio))}
        return Response(content=audio, media_type=mime, headers=headers)


# -------------------------------------------------------------------- jobs
def _register_jobs(app: FastAPI, st: Any) -> None:
    from ..jobs import JobError

    @app.get("/api/jobs")
    def list_jobs() -> dict[str, Any]:
        return {"jobs": st.jobs.list()}

    @app.post("/api/jobs")
    def create_job(body: JobCreate) -> dict[str, Any]:
        try:
            job = st.jobs.create(
                body.name or body.goal[:60], goal=body.goal, kind=body.kind,
                schedule=body.schedule, interval_sec=body.interval_sec,
                cron=body.cron, next_run_at=body.run_at, max_runs=body.max_runs,
                notify=body.notify, max_attempts=body.max_attempts,
                backoff_sec=body.backoff_sec,
                conversation_id=body.conversation_id)
        except JobError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"job": job}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        job = st.jobs.get(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return {"job": job, "runs": st.jobs.runs(job_id)}

    @app.patch("/api/jobs/{job_id}")
    def patch_job(job_id: str, body: JobPatch) -> dict[str, Any]:
        try:
            return {"job": st.jobs.update(job_id, body.model_dump(exclude_none=True))}
        except JobError as exc:
            status = 404 if "Unknown" in str(exc) else 400
            raise HTTPException(status_code=status, detail=str(exc)) from exc

    @app.delete("/api/jobs/{job_id}")
    def delete_job(job_id: str) -> dict[str, Any]:
        if not st.jobs.delete(job_id):
            raise HTTPException(status_code=404, detail="Unknown job.")
        return {"status": "ok"}

    @app.post("/api/jobs/{job_id}/pause")
    def pause_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": st.jobs.pause(job_id)}
        except JobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/resume")
    def resume_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": st.jobs.resume(job_id)}
        except JobError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return {"job": st.jobs.cancel(job_id)}
        except JobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/api/jobs/{job_id}/run")
    def run_job_now(job_id: str) -> dict[str, Any]:
        try:
            return {"job": st.jobs.run_now(job_id)}
        except JobError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/api/jobs/{job_id}/runs")
    def job_runs(job_id: str, limit: int = 20) -> dict[str, Any]:
        if st.jobs.get(job_id) is None:
            raise HTTPException(status_code=404, detail="Unknown job.")
        return {"runs": st.jobs.runs(job_id, limit=limit)}

    @app.get("/api/cron/preview")
    def cron_preview(expression: str = "") -> dict[str, Any]:
        """Validate an expression and show when it would next fire.

        Used by the task form so a typo is caught before the job is saved, and
        so the next run can be shown in the user's own timezone.
        """
        from datetime import datetime

        from ..jobs.cron import CronError, CronExpression
        try:
            parsed = CronExpression(expression)
        except CronError as exc:
            return {"valid": False, "expression": expression, "error": str(exc),
                    "next": [], "next_local": []}
        upcoming = parsed.upcoming(datetime.now(), count=3)
        return {"valid": True, "expression": parsed.expression, "error": None,
                "next": [d.timestamp() for d in upcoming],
                "next_local": [d.strftime("%Y-%m-%d %H:%M") for d in upcoming]}

    @app.get("/api/notifications")
    def list_notifications(unread: bool = False) -> dict[str, Any]:
        items = st.jobs.notifications(unread_only=unread)
        return {"notifications": items,
                "unread": sum(1 for n in items if not n["read"])}

    @app.post("/api/notifications/{notification_id}/read")
    def read_notification(notification_id: str) -> dict[str, Any]:
        if not st.jobs.mark_read(notification_id):
            raise HTTPException(status_code=404, detail="Unknown notification.")
        return {"status": "ok"}

    @app.post("/api/notifications/read-all")
    def read_all_notifications() -> dict[str, Any]:
        return {"marked": st.jobs.mark_all_read()}


# ------------------------------------------------------------------- voice
def _register_voice(app: FastAPI, st: Any) -> None:
    """Wake-word settings, and a way to test the detector without speaking.

    The microphone lives in the daemon, so the live listener is looked up in
    ``sys.modules`` rather than imported: the API must keep working when the
    server runs headless, and importing the daemon from a route would pull the
    whole audio stack into a process that may not have one.
    """
    from .. import config as config_module
    from ..listening.hotword import probe

    backends = [
        {"id": "auto", "label": "Automatic",
         "detail": "Try openWakeWord, then Porcupine."},
        {"id": "openwakeword", "label": "openWakeWord",
         "detail": "Apache-2.0, no account. Needs the openwakeword package."},
        {"id": "porcupine", "label": "Porcupine",
         "detail": "Picovoice. Needs pvporcupine and TOLESH_PORCUPINE_KEY."},
    ]

    def live_listener() -> Any:
        module = sys.modules.get("tolesh.daemon")
        return getattr(module, "_global_voice_listener", None) if module else None

    def current_cfg() -> Any:
        listener = live_listener()
        cfg = getattr(listener, "cfg", None) if listener is not None else None
        return cfg if cfg is not None else config_module.load_settings()

    def snapshot(cfg: Any) -> dict[str, Any]:
        listener = live_listener()
        detector = getattr(listener, "_hotword", None) if listener is not None else None
        status = {
            "listener_running": listener is not None,
            "gating": detector is not None,
            "backend": detector.status() if detector is not None else None,
            "frames_scored": int(getattr(listener, "_hotword_scores", 0) or 0),
            "error": str(getattr(listener, "_hotword_error", "") or ""),
        }
        return {
            "values": {
                "hotword_enabled": bool(getattr(cfg, "hotword_enabled", False)),
                "hotword_backend": str(getattr(cfg, "hotword_backend", "auto")),
                "hotword_model": str(getattr(cfg, "hotword_model", "") or ""),
                "hotword_threshold": float(getattr(cfg, "hotword_threshold", 0.5)),
                "hotword_window_sec": float(getattr(cfg, "hotword_window_sec", 3.0)),
            },
            "status": status,
            "backends": backends,
            "note": ("Gating is off, so every utterance is transcribed and the "
                     "wake word is matched in the text." if not status["gating"]
                     else "Only utterances containing the wake word are transcribed."),
        }

    @app.get("/api/voice/hotword")
    def get_hotword() -> dict[str, Any]:
        return snapshot(current_cfg())

    @app.patch("/api/voice/hotword")
    def patch_hotword(body: dict[str, Any]) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if "hotword_enabled" in body:
            updates["hotword_enabled"] = bool(body["hotword_enabled"])
        if "hotword_backend" in body:
            backend = str(body["hotword_backend"] or "").strip().lower()
            if backend not in ("auto", "openwakeword", "porcupine"):
                raise HTTPException(status_code=400,
                                    detail=f"Unknown backend: {backend!r}")
            updates["hotword_backend"] = backend
        if "hotword_model" in body:
            updates["hotword_model"] = str(body["hotword_model"] or "").strip()[:200]
        if "hotword_threshold" in body:
            try:
                threshold = float(body["hotword_threshold"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400,
                                    detail="Threshold must be a number.") from exc
            if not 0.05 <= threshold <= 0.95:
                raise HTTPException(status_code=400,
                                    detail="Threshold must be between 0.05 and 0.95.")
            updates["hotword_threshold"] = threshold
        if "hotword_window_sec" in body:
            try:
                window = float(body["hotword_window_sec"])
            except (TypeError, ValueError) as exc:
                raise HTTPException(status_code=400,
                                    detail="Window must be a number.") from exc
            if not 0.5 <= window <= 10:
                raise HTTPException(status_code=400,
                                    detail="Window must be between 0.5 and 10 seconds.")
            updates["hotword_window_sec"] = window
        if not updates:
            raise HTTPException(status_code=400, detail="No settings to update.")

        cfg = current_cfg()
        config_module.save_overrides(updates)
        # Settings is a frozen dataclass, so the live configuration is replaced
        # rather than mutated — the listener then reads the new object.
        try:
            import dataclasses

            cfg = dataclasses.replace(cfg, **updates)
        except Exception:
            pass

        listener = live_listener()
        if listener is not None:
            try:
                listener.cfg = cfg
            except Exception:
                pass
        if listener is not None:
            if getattr(listener, "_hotword", None) is not None:
                listener._disable_hotword("settings changed")
            listener._hotword_checked = False
            listener._hotword_error = ""
            if bool(getattr(cfg, "hotword_enabled", False)):
                # Loading a model takes a moment; this is an explicit user
                # action, so paying it here beats a surprise later.
                listener._ensure_hotword()
        return snapshot(cfg)

    @app.post("/api/voice/hotword/test")
    def test_hotword() -> dict[str, Any]:
        """Load the configured backend and score one silent chunk."""
        return probe(current_cfg())


# ---------------------------------------------------------------- telegram
def _register_telegram(app: FastAPI, st: Any) -> None:
    """Turning the bot on, and deciding who may talk to it.

    The token is write-only over the API: it is stored encrypted in the
    connector vault, and every response reports only whether one exists. The
    allowlist is numeric chat ids, because a username can be changed and a chat
    id cannot — and because the check happens before a message reaches the
    pipeline, not inside it.
    """
    from ..channels import BOT_CONNECTION_ID, looks_like_token

    def service() -> Any:
        return getattr(st, "telegram", None)

    def store() -> Any:
        return getattr(st, "telegram_settings", None)

    def token_present() -> bool:
        try:
            secrets = st.connectors.store.secrets(BOT_CONNECTION_ID) or {}
        except Exception:
            return False
        return bool(secrets.get("access_token"))

    def snapshot() -> dict[str, Any]:
        svc, settings = service(), store()
        if svc is None or settings is None:
            return {"available": False, "enabled": False, "allowlist": [],
                    "has_token": False, "status": {"running": False},
                    "note": "This build has no Telegram channel."}
        values = settings.get()
        status = svc.status()
        return {
            "available": True,
            "enabled": bool(values["enabled"]),
            "allowlist": sorted(values["allowlist"]),
            "has_token": token_present(),
            "status": status,
            "note": _telegram_note(values["enabled"], token_present(),
                                  bool(values["allowlist"]), status),
        }

    @app.get("/api/telegram")
    def get_telegram() -> dict[str, Any]:
        return snapshot()

    @app.patch("/api/telegram")
    def patch_telegram(body: TelegramPatch) -> dict[str, Any]:
        svc, settings = service(), store()
        if svc is None or settings is None:
            raise HTTPException(status_code=501,
                                detail="This build has no Telegram channel.")
        if body.token is not None:
            token = body.token.strip()
            if not looks_like_token(token):
                raise HTTPException(
                    status_code=400,
                    detail="A bot token looks like 123456789:AA… — get one from "
                           "@BotFather.")
            # Encrypted at rest by the same vault the OAuth connectors use; the
            # token never appears in the config file or a log line.
            st.connectors.store.upsert(
                connection_id=BOT_CONNECTION_ID, provider="telegram",
                access_token=token, label="Telegram bot", token_type="bot")
        if body.allowlist is not None:
            settings.set_allowlist([int(c) for c in body.allowlist])
        if body.add_chat is not None:
            settings.add_chat(int(body.add_chat))
        if body.remove_chat is not None:
            settings.remove_chat(int(body.remove_chat))
        if body.enabled is not None:
            settings.set_enabled(bool(body.enabled))
            if body.enabled:
                # A restart is the only way a changed token or allowlist takes
                # effect; the poller reads both on every message anyway, but the
                # token is read once at construction.
                svc.start()
            else:
                svc.stop()
        elif body.token is not None and settings.get()["enabled"]:
            svc.start()
        return snapshot()

    @app.post("/api/telegram/start")
    def start_telegram() -> dict[str, Any]:
        svc, settings = service(), store()
        if svc is None or settings is None:
            raise HTTPException(status_code=501,
                                detail="This build has no Telegram channel.")
        if not token_present():
            raise HTTPException(status_code=400,
                                detail="Store a bot token before starting.")
        settings.set_enabled(True)
        svc.start()
        return snapshot()

    @app.post("/api/telegram/stop")
    def stop_telegram() -> dict[str, Any]:
        svc, settings = service(), store()
        if svc is None or settings is None:
            raise HTTPException(status_code=501,
                                detail="This build has no Telegram channel.")
        settings.set_enabled(False)
        svc.stop()
        return snapshot()

    @app.delete("/api/telegram/token")
    def forget_token() -> dict[str, Any]:
        svc, settings = service(), store()
        if svc is None or settings is None:
            raise HTTPException(status_code=501,
                                detail="This build has no Telegram channel.")
        svc.stop()
        settings.set_enabled(False)
        st.connectors.store.delete(BOT_CONNECTION_ID)
        return snapshot()

    @app.post("/api/telegram/test")
    def test_telegram() -> dict[str, Any]:
        """Ask Telegram who this token belongs to, without starting the poller."""
        svc = service()
        if svc is None:
            raise HTTPException(status_code=501,
                                detail="This build has no Telegram channel.")
        if not token_present():
            return {"ok": False, "error": "No bot token is stored."}
        token = (st.connectors.store.secrets(BOT_CONNECTION_ID) or {}).get("access_token")
        try:
            probe = svc.transport_for_test(str(token))
            try:
                info = probe.me() or {}
            finally:
                try:
                    probe.close()
                except Exception:
                    pass
        except Exception as exc:
            return {"ok": False, "error": str(exc)[:300]}
        return {"ok": True, "bot": str(info.get("username") or ""),
                "name": str(info.get("first_name") or ""),
                "id": info.get("id")}


def _telegram_note(enabled: bool, has_token: bool, has_allowlist: bool,
                   status: dict[str, Any]) -> str:
    if not has_token:
        return "No bot token stored. Create a bot with @BotFather and paste the token."
    if not enabled:
        return "Switched off. Nothing is polled."
    if not has_allowlist:
        return ("No chat is allowed yet, so every message is ignored. Send the bot "
                "a message and add the chat id it reports, or use /id from the bot.")
    error = str(status.get("last_error") or "")
    if error:
        return f"Polling, with an error: {error}"
    bot = str(status.get("bot") or "")
    return f"Polling as @{bot}." if bot else "Polling."


# ---------------------------------------------------------------- settings
def _register_settings(app: FastAPI, st: Any) -> None:
    from fastapi.responses import Response

    from ..privacy import collect, delete_all, export_all, summary
    from ..settings import SettingsError

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return st.settings.all()

    @app.patch("/api/settings")
    def patch_settings(body: dict[str, Any]) -> dict[str, Any]:
        try:
            return st.settings.patch(body)
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/api/settings/reset")
    def reset_settings() -> dict[str, Any]:
        return st.settings.reset()

    @app.get("/api/audit")
    def audit(limit: int = 100) -> dict[str, Any]:
        """Every consequential thing this workspace did, newest first."""
        return {"events": collect(st, limit=max(1, min(limit, 500))),
                "summary": summary(st)}

    @app.get("/api/audit/summary")
    def audit_summary() -> dict[str, Any]:
        return summary(st)

    @app.get("/api/export")
    def export() -> Any:
        """Download everything as a zip, with no credentials inside."""
        payload, manifest = export_all(st)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        return Response(content=payload, media_type="application/zip",
                        headers={"Content-Disposition":
                                 f'attachment; filename="tolesh-export-{stamp}.zip"',
                                 "X-Tolesh-Export-Counts":
                                     str(manifest["counts"])})

    @app.post("/api/data/delete")
    def delete_data(body: DataDelete) -> dict[str, Any]:
        result = delete_all(st, body.confirm, include_keys=body.include_keys)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result


# -------------------------------------------------------------- assistants
def _register_assistants(app: FastAPI, st: Any) -> None:
    @app.get("/api/assistants")
    def list_assistants() -> dict[str, Any]:
        return {"assistants": st.assistants.assistants(),
                "templates": st.assistants.templates()}

    @app.post("/api/assistants")
    def create_assistant(body: AssistantCreate) -> dict[str, Any]:
        try:
            return {"assistant": st.assistants.create_assistant(
                body.name, instructions=body.instructions,
                description=body.description, model=body.model,
                greeting=body.greeting, icon=body.icon)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.patch("/api/assistants/{assistant_id}")
    def patch_assistant(assistant_id: str, body: AssistantPatch) -> dict[str, Any]:
        try:
            return {"assistant": st.assistants.update_assistant(
                assistant_id, body.model_dump(exclude_none=True))}
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Unknown assistant.") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/assistants/{assistant_id}")
    def delete_assistant(assistant_id: str) -> dict[str, Any]:
        if not st.assistants.delete_assistant(assistant_id):
            raise HTTPException(status_code=404, detail="Unknown assistant.")
        return {"status": "ok"}

    @app.post("/api/templates")
    def create_template(body: TemplateCreate) -> dict[str, Any]:
        try:
            return {"template": st.assistants.create_template(
                body.name, body.body, category=body.category)}
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/templates/{template_id}")
    def delete_template(template_id: str) -> dict[str, Any]:
        if not st.assistants.delete_template(template_id):
            raise HTTPException(status_code=404, detail="Unknown template.")
        return {"status": "ok"}


# ---------------------------------------------------------------- sharing
def _register_sharing(app: FastAPI, st: Any) -> None:
    from fastapi.responses import HTMLResponse

    from ..workspace import render_error, render_page

    @app.get("/api/shares")
    def list_shares() -> dict[str, Any]:
        return {"shares": st.shares.list(),
                "enabled": bool(st.settings.get("share_links_enabled"))}

    @app.post("/api/conversations/{cid}/share")
    def create_share(cid: str, body: ShareCreate) -> dict[str, Any]:
        """Create a read-only link. Off by default; this is a deliberate act."""
        if not st.settings.get("share_links_enabled"):
            raise HTTPException(
                status_code=403,
                detail="Sharing is switched off. Enable 'share_links_enabled' in "
                       "Settings first.")
        conversation = st.conversations.get(cid)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        share = st.shares.create(cid, title=conversation["title"],
                                 ttl_hours=body.ttl_hours)
        return {"share": share}

    @app.delete("/api/shares/{token}")
    def revoke_share(token: str) -> dict[str, Any]:
        if not st.shares.revoke(token):
            raise HTTPException(status_code=404, detail="Unknown share link.")
        return {"status": "revoked"}

    @app.get("/share/{token}")
    def public_share(token: str) -> Any:
        """The public page for one shared conversation.

        Exempt from the API token guard: the token *is* the credential, and the
        page shows one conversation with no ids, no settings and no workspace.
        """
        share = st.shares.get(token)
        if share is None:
            return HTMLResponse(render_error("This link does not exist."),
                                status_code=404)
        if not share["active"]:
            return HTMLResponse(render_error("This link has been revoked or has expired."),
                                status_code=410)
        conversation = st.conversations.get(share["conversation_id"])
        if conversation is None:
            return HTMLResponse(render_error("The conversation was deleted."),
                                status_code=410)
        st.shares.record_view(token)
        messages = st.conversations.messages(share["conversation_id"], limit=500)
        return HTMLResponse(render_page(share, messages))


# ------------------------------------------------------------ message edits
def _register_editing(app: FastAPI, st: Any) -> None:
    from fastapi.responses import StreamingResponse

    @app.patch("/api/conversations/{cid}/messages/{mid}")
    def edit_message(cid: str, mid: str, body: MessageEdit) -> dict[str, Any]:
        """Replace a message and drop everything that followed it.

        An answer to text the user has since changed is no longer an answer to
        anything, so keeping it would be worse than removing it. The client then
        sends the new question through the normal chat endpoint.
        """
        if st.conversations.get(cid) is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        removed = st.conversations.truncate_from(cid, mid)
        if not removed:
            raise HTTPException(status_code=404, detail="Message not found.")
        created = st.conversations.add_message(cid, "user", body.content)
        return {"status": "ok", "removed": removed, "message": created,
                "messages": st.conversations.messages(cid)}

    @app.delete("/api/conversations/{cid}/messages/{mid}")
    def delete_message(cid: str, mid: str) -> dict[str, Any]:
        if not st.conversations.delete_message(mid):
            raise HTTPException(status_code=404, detail="Message not found.")
        return {"status": "ok", "messages": st.conversations.messages(cid)}

    @app.post("/api/conversations/{cid}/branch")
    def branch(cid: str, body: BranchCreate) -> dict[str, Any]:
        conversation = st.conversations.branch(cid, up_to_message_id=body.message_id,
                                               title=body.title)
        if conversation is None:
            raise HTTPException(status_code=404, detail="Conversation or message not found.")
        return {"conversation": conversation}

    @app.post("/api/conversations/{cid}/regenerate")
    def regenerate(cid: str) -> StreamingResponse:
        """Answer the last question again, replacing the previous answer."""
        if st.conversations.get(cid) is None:
            raise HTTPException(status_code=404, detail="Conversation not found.")
        messages = st.conversations.messages(cid)
        last_user = next((m for m in reversed(messages) if m["role"] == "user"), None)
        if last_user is None:
            raise HTTPException(status_code=400, detail="There is no question to answer.")
        previous = st.conversations.last_assistant_id(cid)
        if previous:
            st.conversations.delete_message(previous)

        from .app import _messages_for
        from .schemas import ChatRequest

        req = ChatRequest(message=last_user["content"], conversation_id=cid)
        blocks: list[str] = []
        prepared = _messages_for(st, req, cid, blocks)

        def gen():
            chunks: list[str] = []
            model_used: str | None = None
            try:
                yield _sse({"type": "meta", "data": {"conversation_id": cid,
                                                     "regenerated": True}})
                for event in st.router.stream(prepared, task=req.task, model=req.model,
                                              temperature=req.temperature,
                                              max_tokens=req.max_tokens):
                    if event.type == "token":
                        chunks.append(str(event.data))
                    elif event.type == "reset":
                        chunks.clear()
                    elif event.type in ("usage", "done") and event.model_id:
                        model_used = event.model_id
                    yield _sse(event.as_dict())
                text = "".join(chunks)
                if text:
                    st.conversations.add_message(cid, "assistant", text,
                                                 model=model_used,
                                                 tokens=max(1, len(text) // 4))
            except Exception as exc:
                yield _sse({"type": "error", "data": {"message": str(exc)}})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})
