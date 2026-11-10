"""Tolesh shared API server.

Run from the repo root (standalone, for the web client or debugging):
    PYTHONPATH=src uvicorn tolesh.server.app:app --host 127.0.0.1 --port 8787

In the desktop product this server is started *inside* the application by
:class:`tolesh.server.embedded.EmbeddedServer`, which the PyQt6 shell owns for
the whole process lifetime. The user never launches uvicorn by hand.

The same JSON/SSE contract is consumed by:
  * the desktop QtWebEngine shell (src/desktop_app/ai_window.py)
  * a future Cloudflare Pages web client
  * a future Android client

Security notes:
  * provider API keys never appear in any response (Registry.redacted()).
  * optional TOLESH_API_TOKEN (or an injected token) gates every /api route
    except health.
  * CORS origins are explicit, not wildcards with credentials.
  * consequential computer actions require a human approval token.
"""

from __future__ import annotations

import json
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..ai import ModelRouter, Registry, UsageLedger
from ..conversation import ConversationManager
from ..memory_ext import LayeredMemory
from ..permissions import PermissionGate
from ..persona import build_system_prompt
from ..tools_ext import ComputerTools
from .schemas import (
    ChatRequest, ConfigPatch, ConversationCreate, ConversationRename, MemoryCreate,
    MemoryForget, PermissionDecision, ProviderPatch, ToolInvoke,
)

VERSION = "0.1.0"
DEFAULT_WEB_DIR = Path(__file__).resolve().parents[3] / "web"


def default_state_db_path() -> str:
    """Persistent database used when no explicit path is supplied.

    Resolved through the shared helper so the desktop shell, this server and
    the inherited daemon all point at ``<data dir>/tolesh.db``.
    """
    from .._sqlite import default_db_path

    return default_db_path()


class AppState:
    def __init__(self, *, db_path: str | None = None, in_memory: bool = False):
        # Persistent by default: an in-memory default silently discarded every
        # conversation and memory item on restart, which is exactly the bug the
        # unified desktop application had to fix. Tests opt into ``in_memory``.
        path = ":memory:" if in_memory else (db_path or default_state_db_path())
        self.registry = Registry.from_defaults()
        self.ledger = UsageLedger(in_memory=in_memory, db_path=path)
        self.router = ModelRouter(self.registry, self.ledger)
        self.memory = LayeredMemory(db_path=path)
        self.conversations = ConversationManager(db_path=path)
        self.gate = PermissionGate(db_path=path)
        self.tools = ComputerTools(self.gate)
        self.db_path = path

    def close(self) -> None:
        for obj in (self.ledger, self.memory, self.conversations, self.gate):
            try:
                obj.close()
            except Exception:
                pass


def _messages_for(state: AppState, req: ChatRequest, conversation_id: str) -> list[dict[str, str]]:
    spec = state.router.select(req.task, req.model)
    label = spec.label if spec else "Tolesh Offline (local)"
    system = build_system_prompt(state.memory.build_context(), task=req.task,
                                 model_label=label)
    window = state.conversations.window(conversation_id, limit=20)
    return [{"role": "system", "content": system}, *window,
            {"role": "user", "content": req.message}]


def _allowed_origins() -> list[str]:
    raw = os.environ.get("TOLESH_ALLOWED_ORIGINS", "")
    base = ["http://127.0.0.1:8787", "http://localhost:8787",
            "http://127.0.0.1:5173", "http://localhost:5173"]
    return base + [o.strip() for o in raw.split(",") if o.strip()]


def create_app(*, state: AppState | None = None, web_dir: str | Path | None = None,
               serve_static: bool = True, api_token: str | None = None) -> FastAPI:
    st = state or AppState()
    web = Path(web_dir or os.environ.get("TOLESH_WEB_DIR") or DEFAULT_WEB_DIR)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        # Do not close a caller-supplied state (tests may still use it).
        if state is None:
            st.close()

    app = FastAPI(title="Tolesh API", version=VERSION, lifespan=lifespan)
    app.state.tolesh = st

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins(),
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization"],
    )

    api_token = os.environ.get("TOLESH_API_TOKEN", "") if api_token is None else api_token

    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        if api_token and request.url.path.startswith("/api/") \
                and not request.url.path.startswith("/api/health"):
            supplied = request.headers.get("Authorization", "")
            if supplied != f"Bearer {api_token}":
                return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)

    # ---------------------------------------------------------------- meta
    @app.get("/api/health")
    def health() -> dict[str, Any]:
        configured = [p.name for p in st.registry.providers.values() if p.configured]
        return {"status": "ok", "version": VERSION, "product": "Tolesh",
                "providers_configured": configured,
                "default_model": st.registry.default_model or None,
                "database": st.db_path,
                "static_frontend": web.is_dir(),
                "time": time.time()}

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return st.registry.redacted()

    @app.get("/api/providers")
    def providers() -> dict[str, Any]:
        return st.registry.redacted()

    @app.get("/api/models")
    def models(task: str | None = None, provider: str | None = None) -> dict[str, Any]:
        return {"models": [m.as_dict() for m in st.registry.models(task=task, provider=provider)]}

    # --------------------------------------------------------- configuration
    @app.patch("/api/config")
    def patch_config(body: ConfigPatch) -> dict[str, Any]:
        """Persist the user's default-model choice.

        An empty string means "auto routing"; a model id must exist in the
        registry so a typo cannot silently disable the whole chain.
        """
        if body.default_model is not None:
            try:
                st.registry.set_default_model(body.default_model, persist=True)
            except KeyError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
        return st.registry.redacted()

    @app.patch("/api/providers/{name}")
    def patch_provider(name: str, body: ProviderPatch) -> dict[str, Any]:
        """Update one provider's endpoint/key/state and persist it.

        The API key is written to the per-user providers file with 0600
        permissions and is never echoed back — the response is redacted.
        """
        patch = body.model_dump(exclude_none=True)
        if not patch:
            raise HTTPException(status_code=400, detail="No fields to update")
        try:
            spec = st.registry.update_provider(name, patch, persist=True)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {name}") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"provider": spec.as_dict(redact=True)}

    @app.post("/api/providers/{name}/test")
    def test_provider(name: str) -> dict[str, Any]:
        """Make one tiny real request and report what actually answered.

        This is deliberately honest: the router falls back to the offline
        engine, so a completion whose model id belongs to a *different*
        provider is reported as ``fallback`` rather than as success.
        """
        spec = st.registry.provider(name)
        if spec is None:
            raise HTTPException(status_code=404, detail=f"Unknown provider: {name}")
        if not spec.configured:
            return {"ok": False, "status": "unconfigured",
                    "detail": f"{spec.label} has no base URL or API key yet."}
        if not spec.models:
            return {"ok": False, "status": "no_models",
                    "detail": f"{spec.label} declares no models."}
        model = spec.models[0]
        try:
            result = st.router.complete(
                [{"role": "user", "content": "Reply with the single word: ready"}],
                task="fast", model=model.id, max_tokens=16, temperature=0.0,
            )
        except Exception as exc:
            return {"ok": False, "status": "error", "detail": str(exc)[:400]}
        if not str(result.model_id or "").startswith(f"{name}:"):
            return {"ok": False, "status": "fallback",
                    "detail": f"{spec.label} did not answer; the chain fell back to "
                              f"{result.model_id}.",
                    "model": result.model_id}
        return {"ok": True, "status": "ok", "model": result.model_id,
                "reply": result.text[:200], "usage": result.usage.as_dict()}

    # ----------------------------------------------------------------- chat
    @app.post("/api/chat")
    def chat(req: ChatRequest) -> StreamingResponse:
        cid = req.conversation_id or st.conversations.create()["id"]
        st.conversations.add_message(cid, "user", req.message)
        messages = _messages_for(st, req, cid)

        def gen():
            chunks: list[str] = []
            model_used: str | None = None
            try:
                yield _sse({"type": "meta", "data": {"conversation_id": cid}})
                for ev in st.router.stream(messages, task=req.task, model=req.model,
                                           temperature=req.temperature,
                                           max_tokens=req.max_tokens):
                    payload = ev.as_dict()
                    if ev.type == "token":
                        chunks.append(str(ev.data))
                    elif ev.type == "reset":
                        chunks.clear()
                    elif ev.type in ("usage", "done") and ev.model_id:
                        model_used = ev.model_id
                    yield _sse(payload)
                text = "".join(chunks)
                if text:
                    st.conversations.add_message(cid, "assistant", text,
                                                 model=model_used,
                                                 tokens=max(1, len(text) // 4))
            except Exception as exc:  # keep the SSE stream well-formed
                yield _sse({"type": "error", "data": {"message": str(exc)}})

        return StreamingResponse(gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache",
                                          "X-Accel-Buffering": "no"})

    @app.post("/api/chat/sync")
    def chat_sync(req: ChatRequest) -> dict[str, Any]:
        cid = req.conversation_id or st.conversations.create()["id"]
        st.conversations.add_message(cid, "user", req.message)
        messages = _messages_for(st, req, cid)
        try:
            result = st.router.complete(messages, task=req.task, model=req.model,
                                        temperature=req.temperature,
                                        max_tokens=req.max_tokens)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        st.conversations.add_message(cid, "assistant", result.text,
                                     model=result.model_id,
                                     tokens=result.usage.total_tokens)
        return {"conversation_id": cid, "text": result.text,
                "model": result.model_id, "usage": result.usage.as_dict()}

    # --------------------------------------------------------- conversations
    @app.get("/api/conversations")
    def list_conversations() -> dict[str, Any]:
        return {"conversations": st.conversations.list()}

    @app.post("/api/conversations")
    def create_conversation(body: ConversationCreate) -> dict[str, Any]:
        return st.conversations.create(body.title)

    @app.get("/api/conversations/{cid}/messages")
    def get_messages(cid: str) -> dict[str, Any]:
        return {"messages": st.conversations.messages(cid)}

    @app.patch("/api/conversations/{cid}")
    def rename_conversation(cid: str, body: ConversationRename) -> dict[str, Any]:
        if not st.conversations.rename(cid, body.title):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "ok"}

    @app.delete("/api/conversations/{cid}")
    def delete_conversation(cid: str) -> dict[str, Any]:
        if not st.conversations.delete(cid):
            raise HTTPException(status_code=404, detail="Conversation not found")
        return {"status": "ok"}

    # --------------------------------------------------------------- memory
    @app.get("/api/memory")
    def list_memory(category: str | None = None, q: str | None = None) -> dict[str, Any]:
        items = st.memory.search(q) if q else st.memory.list(category=category)
        return {"items": items, "stats": st.memory.stats()}

    @app.post("/api/memory")
    def add_memory(body: MemoryCreate) -> dict[str, Any]:
        try:
            return st.memory.add(body.category, body.content,
                                 source=body.source, pinned=body.pinned)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.delete("/api/memory/{item_id}")
    def delete_memory(item_id: str) -> dict[str, Any]:
        if not st.memory.delete(item_id):
            raise HTTPException(status_code=404, detail="Memory item not found")
        return {"status": "ok", "id": item_id}

    @app.post("/api/memory/forget")
    def forget_memory(body: MemoryForget) -> dict[str, Any]:
        result = st.memory.forget_everything(body.confirm)
        if result["status"] == "error":
            raise HTTPException(status_code=400, detail=result["message"])
        return result

    # ---------------------------------------------------------------- usage
    @app.get("/api/usage")
    def usage() -> dict[str, Any]:
        return st.ledger.summary()

    # ---------------------------------------------------------- permissions
    @app.get("/api/permissions/pending")
    def pending_permissions() -> dict[str, Any]:
        return {"pending": st.gate.pending()}

    @app.get("/api/permissions/audit")
    def permission_audit() -> dict[str, Any]:
        return {"entries": st.gate.audit_log()}

    @app.post("/api/permissions/{action_id}/approve")
    def approve_permission(action_id: str, body: PermissionDecision) -> dict[str, Any]:
        decision = st.gate.approve(action_id, body.token)
        if decision["status"] == "error":
            raise HTTPException(status_code=400, detail=decision["message"])
        if body.execute:
            return {"decision": decision,
                    "execution": st.tools.execute(decision["tool"], decision["args"],
                                                 risk=decision["risk"])}
        return {"decision": decision}

    @app.post("/api/permissions/{action_id}/deny")
    def deny_permission(action_id: str, body: PermissionDecision) -> dict[str, Any]:
        decision = st.gate.deny(action_id, body.token)
        if decision["status"] == "error":
            raise HTTPException(status_code=400, detail=decision["message"])
        return {"decision": decision}

    # ---------------------------------------------------------------- tools
    @app.get("/api/tools")
    def list_tools() -> dict[str, Any]:
        return {"tools": st.tools.list_tools()}

    @app.post("/api/tools/invoke")
    def invoke_tool(body: ToolInvoke) -> dict[str, Any]:
        return st.tools.invoke(body.tool, body.args, reason=body.reason)

    # ---------------------------------------------------------- static web
    if serve_static and web.is_dir():
        app.mount("/", StaticFiles(directory=str(web), html=True), name="web")

    return app


def _sse(payload: dict[str, Any]) -> str:
    return "data: " + json.dumps(payload, ensure_ascii=False) + "\n\n"


# Module-level ASGI app for `uvicorn tolesh.server.app:app`.
#
# Construction is deferred to the first ASGI call so that merely importing
# this module (tests, tooling, type checks) does not create a database file
# and four live SQLite connections as a side effect.
class _LazyApp:
    def __init__(self) -> None:
        self._app: FastAPI | None = None

    def __call__(self, scope, receive, send):
        if self._app is None:
            self._app = create_app()
        return self._app(scope, receive, send)


app = _LazyApp()
