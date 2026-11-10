"""MCP client manager: one live session per configured server.

Why a background event loop rather than ``asyncio.run`` per call: several MCP
servers own external state (a browser, a database handle, a login). Opening
and closing a session on every tool call destroys that state. The same reason
is documented in ``tools/external/mcp_runtime.spec.md`` for the voice daemon;
this is the server-side equivalent, with resources, prompts and permissions
added because the web workspace needs them.

Transports:
  * ``stdio`` — the SDK's ``stdio_client``; the process is kept resident.
  * ``http``  — the SDK's ``streamablehttp_client`` (the current MCP HTTP
    transport). Static headers cover bearer-token servers.

Not implemented, and reported as such rather than half-working: the legacy
HTTP+SSE transport (2024-11-05) and OAuth 2.1 dynamic client registration.
Both need a browser callback flow; see docs/MCP.md.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from contextlib import AsyncExitStack
from datetime import timedelta
from typing import Any

from .store import MCPServerStore, mask_secret

try:  # pragma: no cover - exercised by import failure only
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamablehttp_client

    SDK_AVAILABLE = True
except Exception:  # pragma: no cover
    ClientSession = None  # type: ignore[assignment]
    SDK_AVAILABLE = False


class MCPError(RuntimeError):
    """A server answered, but the answer was an error."""

    def __init__(self, message: str, *, server: str = "", code: Any = None):
        super().__init__(message)
        self.server = server
        self.code = code


class MCPUnavailable(MCPError):
    """The server could not be reached at all."""


def _text_of(item: Any) -> str:
    """Flatten one MCP content item to text without losing its kind."""
    kind = getattr(item, "type", None) or (item.get("type") if isinstance(item, dict) else None)
    if kind == "text" or hasattr(item, "text"):
        return str(getattr(item, "text", "") or "")
    if kind in ("image", "audio"):
        mime = getattr(item, "mimeType", "") or ""
        return f"[{kind} content{f' {mime}' if mime else ''}]"
    if kind == "resource":
        res = getattr(item, "resource", None)
        if isinstance(res, dict):
            return str(res.get("text") or f"[resource {res.get('uri')}]")
        return str(getattr(res, "text", "") or f"[resource {getattr(res, 'uri', '')}]")
    if isinstance(item, dict):
        return str(item.get("text") or "")
    return str(item)


def _content_text(result: Any) -> str:
    items = getattr(result, "content", None)
    if items is None and isinstance(result, dict):
        items = result.get("content")
    if not items:
        structured = getattr(result, "structuredContent", None)
        if structured is None and isinstance(result, dict):
            structured = result.get("structuredContent")
        return "" if structured is None else str(structured)
    return "\n".join(t for t in (_text_of(i) for i in items) if t)


class _Session:
    """One open connection, owned by the manager's event loop."""

    def __init__(self, *, cfg: dict[str, Any], stack: AsyncExitStack,
                 session: Any, init: Any, tools: list[dict[str, Any]],
                 resources: list[dict[str, Any]], prompts: list[dict[str, Any]],
                 lock: asyncio.Lock):
        self.cfg = cfg
        self.stack = stack
        self.session = session
        self.init = init
        self.tools = tools
        self.resources = resources
        self.prompts = prompts
        self.lock = lock
        self.opened_at = time.time()

    @property
    def server_info(self) -> dict[str, Any]:
        info = getattr(self.init, "serverInfo", None)
        return {
            "name": getattr(info, "name", "") or "",
            "version": getattr(info, "version", "") or "",
            "protocol": getattr(self.init, "protocolVersion", "") or "",
        }

    @property
    def capabilities(self) -> dict[str, Any]:
        caps = getattr(self.init, "capabilities", None)
        if caps is None:
            return {}
        out: dict[str, Any] = {}
        for key in ("tools", "resources", "prompts", "logging", "sampling"):
            if getattr(caps, key, None) is not None:
                out[key] = True
        return out


class MCPManager:
    """Connect, discover and invoke, with permissions and a call log."""

    def __init__(self, store: MCPServerStore | None = None, *,
                 gate: Any = None, call_timeout: float = 60.0):
        self.store = store or MCPServerStore()
        self.gate = gate
        self.call_timeout = call_timeout
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._sessions: dict[str, _Session] = {}
        self._locks: dict[str, threading.Lock] = {}
        self._status: dict[str, dict[str, Any]] = {}
        self._log: list[dict[str, Any]] = []

    # ------------------------------------------------------------- plumbing
    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._loop_lock:
            if self._loop is not None and self._loop.is_running():
                return self._loop
            ready = threading.Event()

            def run() -> None:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                self._loop = loop
                ready.set()
                loop.run_forever()

            self._thread = threading.Thread(target=run, name="tolesh-mcp", daemon=True)
            self._thread.start()
            ready.wait(5)
            assert self._loop is not None
            return self._loop

    def _call(self, coro: Any, timeout: float | None = None) -> Any:
        loop = self._ensure_loop()
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout or self.call_timeout)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise MCPError("The MCP server did not answer in time.") from None
        except concurrent.futures.CancelledError:
            # asyncio marks the concurrent future cancelled when the task is
            # cancelled mid-flight (a transport teardown does this). Callers
            # must see an MCP error they can report, not a bare CancelledError.
            raise MCPError("The MCP connection was cancelled before it completed.") from None

    def _server_lock(self, name: str) -> threading.Lock:
        with self._loop_lock:
            return self._locks.setdefault(name, threading.Lock())

    # ------------------------------------------------------------ lifecycle
    def _cfg(self, name: str) -> dict[str, Any]:
        cfg = self.store.raw(name)
        if cfg is None:
            raise MCPError(f"Unknown MCP server: {name}", server=name)
        if not cfg.get("enabled", True):
            raise MCPUnavailable(f"{name} is switched off.", server=name)
        return cfg

    async def _open(self, cfg: dict[str, Any]) -> _Session:
        if not SDK_AVAILABLE:  # pragma: no cover
            raise MCPUnavailable(
                "The 'mcp' Python package is not installed, so MCP servers "
                "cannot be started.", server=cfg["name"])
        name = cfg["name"]
        timeout = float(cfg.get("timeout_sec") or 30)
        stack = AsyncExitStack()
        try:
            if cfg.get("transport") == "stdio":
                params = StdioServerParameters(
                    command=cfg["command"],
                    args=list(cfg.get("args") or []),
                    env={**dict(cfg.get("env") or {})} or None,
                )
                read, write = await stack.enter_async_context(stdio_client(params))
            else:
                read, write, _get_sid = await stack.enter_async_context(
                    streamablehttp_client(
                        cfg["url"],
                        headers=dict(cfg.get("headers") or {}) or None,
                        timeout=timeout,
                        sse_read_timeout=max(timeout, 60),
                    )
                )
            session = await stack.enter_async_context(
                ClientSession(read, write,
                              read_timeout_seconds=timedelta(seconds=timeout)))
            init = await asyncio.wait_for(session.initialize(), timeout=timeout)
            tools: list[dict[str, Any]] = []
            resources: list[dict[str, Any]] = []
            prompts: list[dict[str, Any]] = []
            try:
                listed = await asyncio.wait_for(session.list_tools(), timeout=timeout)
                tools = [_tool_dict(t) for t in (listed.tools or [])]
            except Exception:
                # A server may legitimately declare no tool capability.
                tools = []
            try:
                listed_r = await asyncio.wait_for(session.list_resources(), timeout=timeout)
                resources = [_resource_dict(r) for r in (listed_r.resources or [])]
            except Exception:
                resources = []
            try:
                listed_p = await asyncio.wait_for(session.list_prompts(), timeout=timeout)
                prompts = [_prompt_dict(p) for p in (listed_p.prompts or [])]
            except Exception:
                prompts = []
            return _Session(cfg=cfg, stack=stack, session=session, init=init,
                            tools=tools, resources=resources, prompts=prompts,
                            lock=asyncio.Lock())
        except Exception as exc:
            try:
                await stack.aclose()
            except Exception:
                pass
            raise MCPUnavailable(_explain(exc, name), server=name) from exc

    def connect(self, name: str, *, force: bool = False) -> dict[str, Any]:
        """Open (or reuse) the session and return what the server offers."""
        cfg = self._cfg(name)
        with self._server_lock(name):
            session = self._sessions.get(name)
            if session is not None and not force:
                return self._describe(session)
            if session is not None:
                self._close_session(name)

            async def _open() -> _Session:
                return await self._open(cfg)

            try:
                session = self._call(_open(), timeout=float(cfg.get("timeout_sec") or 30) + 10)
            except MCPError as exc:
                self._status[name] = {"state": "error", "error": str(exc),
                                      "checked_at": time.time(), "tools": 0}
                raise
            except Exception as exc:  # a transport bug must still be reportable
                error = MCPUnavailable(_explain(exc, name), server=name)
                self._status[name] = {"state": "error", "error": str(error),
                                      "checked_at": time.time(), "tools": 0}
                raise error from exc
            self._sessions[name] = session
            self._status[name] = {"state": "connected", "error": "",
                                  "checked_at": time.time(),
                                  "tools": len(session.tools),
                                  "resources": len(session.resources),
                                  "prompts": len(session.prompts)}
            return self._describe(session)

    def _describe(self, session: _Session) -> dict[str, Any]:
        name = session.cfg["name"]
        cfg = session.cfg
        tools = []
        for tool in session.tools:
            tools.append({**tool,
                          "permission": self.store.policy_for(cfg, tool["name"]),
                          "qualified": tool_id(name, tool["name"])})
        return {"server": name, "transport": cfg.get("transport"),
                "server_info": session.server_info,
                "capabilities": session.capabilities,
                "tools": tools,
                "resources": session.resources,
                "prompts": session.prompts,
                "connected_at": session.opened_at,
                "status": self._status.get(name, {})}

    def _close_session(self, name: str) -> None:
        session = self._sessions.pop(name, None)
        if session is None:
            return

        async def _close() -> None:
            try:
                await session.stack.aclose()
            except Exception:
                pass

        try:
            self._call(_close(), timeout=10)
        except Exception:
            pass

    def disconnect(self, name: str) -> dict[str, Any]:
        self._close_session(name)
        self._status[name] = {"state": "disconnected", "checked_at": time.time(),
                              "tools": 0, "error": ""}
        return {"server": name, "status": self._status[name]}

    def close(self) -> None:
        for name in list(self._sessions):
            self._close_session(name)
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        self._loop = None

    # --------------------------------------------------------------- status
    def status(self, name: str | None = None) -> Any:
        servers = {s["name"]: s for s in self.store.list(redact=True)}
        names = [name] if name else sorted(servers)
        out = []
        for n in names:
            cfg = servers.get(n)
            if cfg is None:
                raise MCPError(f"Unknown MCP server: {n}", server=n)
            entry = dict(self._status.get(n) or {})
            if not entry:
                entry = {"state": "disabled" if not cfg["enabled"] else "idle",
                         "checked_at": None, "tools": 0, "error": ""}
            out.append({"server": n, "transport": cfg["transport"],
                        "enabled": cfg["enabled"], "configured": cfg["configured"],
                        **entry})
        return out[0] if name else out

    def test(self, name: str) -> dict[str, Any]:
        """Really connect, list, and report — a connectivity test, not a ping."""
        started = time.time()
        try:
            info = self.connect(name, force=True)
        except MCPError as exc:
            return {"ok": False, "server": name, "error": str(exc),
                    "duration_ms": int((time.time() - started) * 1000)}
        return {"ok": True, "server": name,
                "server_info": info["server_info"],
                "tools": len(info["tools"]),
                "resources": len(info["resources"]),
                "prompts": len(info["prompts"]),
                "duration_ms": int((time.time() - started) * 1000)}

    # ---------------------------------------------------------------- tools
    def all_tools(self, *, enabled_only: bool = True) -> list[dict[str, Any]]:
        """Every tool on every enabled server, without opening new sessions.

        A server that was never connected is connected here, because a tool
        catalogue that silently omits half the servers is worse than a slow
        one.
        """
        out: list[dict[str, Any]] = []
        for cfg in self.store.list(redact=True):
            if enabled_only and not cfg["enabled"]:
                continue
            if not cfg["configured"]:
                continue
            try:
                info = self.connect(cfg["name"])
            except MCPError:
                continue
            for tool in info["tools"]:
                out.append({"server": cfg["name"], **tool})
        return out

    def invoke(self, name: str, tool: str, args: dict[str, Any] | None = None,
               *, approval: dict[str, Any] | None = None,
               reason: str = "") -> dict[str, Any]:
        """Call a tool, honouring the per-tool permission policy.

        ``allow`` runs immediately. ``ask`` parks a pending action with the
        permission gate and returns it; the caller approves and retries with
        ``approval``. ``deny`` never runs.
        """
        cfg = self._cfg(name)
        args = args or {}
        policy = self.store.policy_for(cfg, tool)
        if policy == "deny":
            self._audit(name, tool, args, "denied", "policy: deny")
            return {"status": "denied", "server": name, "tool": tool,
                    "message": f"{tool} is blocked by this server's policy."}
        if policy == "ask" and not approval:
            if self.gate is None:
                return {"status": "denied", "server": name, "tool": tool,
                        "message": "This tool needs approval but no approval gate is available."}
            decision = self.gate.request(
                tool_id(name, tool), args,
                reason=reason or f"MCP tool {tool} on {name} needs approval.")
            return {"status": "pending-approval", "server": name, "tool": tool,
                    "args": args, "approval": decision}
        if approval and str(approval.get("status")) != "approved":
            self._audit(name, tool, args, "denied", "approval not granted")
            return {"status": "denied", "server": name, "tool": tool,
                    "message": "The approval was not granted."}

        started = time.time()
        try:
            info = self.connect(name)
        except MCPError as exc:
            self._audit(name, tool, args, "error", str(exc))
            return {"status": "error", "server": name, "tool": tool,
                    "message": str(exc)}
        known = {t["name"] for t in info["tools"]}
        if known and tool not in known:
            self._audit(name, tool, args, "error", "unknown tool")
            return {"status": "error", "server": name, "tool": tool,
                    "message": f"{name} does not expose a tool called {tool}.",
                    "available": sorted(known)}
        session = self._sessions.get(name)
        if session is None:  # pragma: no cover - connect() guarantees this
            return {"status": "error", "server": name, "tool": tool,
                    "message": "The session closed before the call."}

        async def _invoke() -> Any:
            async with session.lock:
                return await asyncio.wait_for(
                    session.session.call_tool(tool, args),
                    timeout=float(cfg.get("timeout_sec") or 30))

        try:
            result = self._call(_invoke())
        except MCPError as exc:
            # One reconnect for a dropped session; a second failure is reported.
            try:
                self.connect(name, force=True)
                session = self._sessions[name]

                async def _retry() -> Any:
                    async with session.lock:
                        return await asyncio.wait_for(
                            session.session.call_tool(tool, args),
                            timeout=float(cfg.get("timeout_sec") or 30))

                result = self._call(_retry())
            except MCPError as second:
                self._audit(name, tool, args, "error", str(second))
                return {"status": "error", "server": name, "tool": tool,
                        "message": str(second), "first_error": str(exc)}
        except Exception as exc:
            self._audit(name, tool, args, "error", str(exc))
            return {"status": "error", "server": name, "tool": tool,
                    "message": _explain(exc, name)}

        is_error = bool(getattr(result, "isError", False))
        text = _content_text(result)
        status = "error" if is_error else "ok"
        self._audit(name, tool, args, status, text[:200])
        return {"status": status, "server": name, "tool": tool,
                "text": text, "is_error": is_error,
                "duration_ms": int((time.time() - started) * 1000)}

    # ------------------------------------------------------------ resources
    def resources(self, name: str) -> list[dict[str, Any]]:
        info = self.connect(name)
        return info["resources"]

    def read_resource(self, name: str, uri: str) -> dict[str, Any]:
        self.connect(name)
        session = self._sessions[name]

        async def _read() -> Any:
            async with session.lock:
                return await asyncio.wait_for(session.session.read_resource(uri),
                                              timeout=float(session.cfg.get("timeout_sec") or 30))

        try:
            result = self._call(_read())
        except MCPError:
            raise
        except Exception as exc:
            raise MCPError(_explain(exc, name), server=name) from exc
        contents = getattr(result, "contents", None) or []
        parts = []
        for item in contents:
            text = getattr(item, "text", None)
            if text is None:
                text = f"[binary resource {getattr(item, 'mimeType', '')}]"
            parts.append(str(text))
        return {"server": name, "uri": uri, "text": "\n\n".join(parts),
                "chars": sum(len(p) for p in parts)}

    # -------------------------------------------------------------- prompts
    def prompts(self, name: str) -> list[dict[str, Any]]:
        return self.connect(name)["prompts"]

    def get_prompt(self, name: str, prompt: str,
                   args: dict[str, Any] | None = None) -> dict[str, Any]:
        self.connect(name)
        session = self._sessions[name]

        async def _get() -> Any:
            async with session.lock:
                return await asyncio.wait_for(
                    session.session.get_prompt(prompt, args or {}),
                    timeout=float(session.cfg.get("timeout_sec") or 30))

        try:
            result = self._call(_get())
        except Exception as exc:
            raise MCPError(_explain(exc, name), server=name) from exc
        messages = []
        for msg in getattr(result, "messages", None) or []:
            content = getattr(msg, "content", None)
            messages.append({"role": getattr(msg, "role", "user"),
                             "text": _text_of(content)})
        return {"server": name, "prompt": prompt, "messages": messages}

    # ---------------------------------------------------------------- audit
    def _audit(self, server: str, tool: str, args: dict[str, Any],
               status: str, detail: str) -> None:
        self._log.append({
            "server": server, "tool": tool,
            # Arguments can carry a document or a token; keep a bounded preview.
            "args": {k: (mask_secret(str(v)) if any(
                s in k.lower() for s in ("key", "token", "secret", "password"))
                else str(v)[:120]) for k, v in args.items()},
            "status": status, "detail": detail[:300], "at": time.time(),
        })
        del self._log[:-200]

    def calls(self, limit: int = 50) -> list[dict[str, Any]]:
        return list(reversed(self._log[-limit:]))


def tool_id(server: str, tool: str) -> str:
    return f"mcp__{server}__{tool}"


def split_tool_id(qualified: str) -> tuple[str, str] | None:
    if not qualified.startswith("mcp__"):
        return None
    rest = qualified[len("mcp__"):]
    if "__" not in rest:
        return None
    server, tool = rest.split("__", 1)
    return server, tool


def _tool_dict(tool: Any) -> dict[str, Any]:
    return {
        "name": getattr(tool, "name", "") or "",
        "description": (getattr(tool, "description", "") or "")[:1000],
        "input_schema": getattr(tool, "inputSchema", None) or {},
    }


def _resource_dict(res: Any) -> dict[str, Any]:
    return {
        "uri": str(getattr(res, "uri", "") or ""),
        "name": getattr(res, "name", "") or "",
        "description": (getattr(res, "description", "") or "")[:500],
        "mime_type": getattr(res, "mimeType", "") or "",
    }


def _prompt_dict(prompt: Any) -> dict[str, Any]:
    args = []
    for arg in getattr(prompt, "arguments", None) or []:
        args.append({"name": getattr(arg, "name", ""),
                     "description": getattr(arg, "description", "") or "",
                     "required": bool(getattr(arg, "required", False))})
    return {"name": getattr(prompt, "name", "") or "",
            "description": (getattr(prompt, "description", "") or "")[:500],
            "arguments": args}


def _explain(exc: Exception, server: str) -> str:
    text = str(exc) or exc.__class__.__name__
    lowered = text.lower()
    if "filenotfound" in lowered or "no such file" in lowered:
        return (f"{server}: the server command was not found. Check the command "
                "path and that its runtime (node/uvx/python) is installed.")
    if "connection refused" in lowered or "connect" in lowered:
        return f"{server}: could not connect ({text[:200]})."
    if "timed out" in lowered or isinstance(exc, asyncio.TimeoutError):
        return f"{server}: the server did not answer in time."
    return f"{server}: {text[:300]}"
