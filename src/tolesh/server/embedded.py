"""In-process lifecycle manager for the Tolesh API server.

The desktop application must be *one* application. Before this module existed
the FastAPI backend was a separate `uvicorn` process the user had to start by
hand, which is why the shipped product had three disconnected pieces: the
PyQt6 shell, a headless voice daemon, and a web-only backend.

:class:`EmbeddedServer` runs the same ASGI app that ``uvicorn
tolesh.server.app:app`` serves, but inside a daemon thread owned by the desktop
process. It:

  * binds an ephemeral loopback port (no fixed-port clashes, no "port in use")
  * waits until ``/api/health`` actually answers before reporting ready
  * uses the persistent database resolved by :func:`tolesh._sqlite.default_db_path`
  * injects a per-run bearer token so other local processes cannot drive the
    assistant, and hands the same token to the desktop UI
  * shuts the ASGI app and its SQLite handles down on ``stop()``
  * refuses to start twice in one process

Nothing here imports Qt, so it is testable headlessly.
"""

from __future__ import annotations

import json
import os
import secrets
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from .app import AppState, create_app


def resolve_web_dir(explicit: str | Path | None = None) -> Path:
    """Locate the static frontend for dev runs and frozen bundles alike."""
    if explicit:
        return Path(explicit)
    env = os.environ.get("TOLESH_WEB_DIR")
    if env:
        return Path(env)
    # PyInstaller onedir/onefile unpack location (datas land at <MEIPASS>/web).
    bundle = getattr(sys, "_MEIPASS", None)
    if bundle:
        candidate = Path(bundle) / "web"
        if candidate.is_dir():
            return candidate
    # Repo layout: <root>/src/tolesh/server/embedded.py -> <root>/web
    return Path(__file__).resolve().parents[3] / "web"


class EmbeddedServer:
    """Owns one uvicorn server for the lifetime of the desktop application."""

    #: Guard against two backends inside one process (e.g. a double click on
    #: the tray action racing the startup path).
    _instance: "EmbeddedServer | None" = None
    _instance_lock = threading.Lock()

    def __init__(
        self,
        *,
        db_path: str | Path | None = None,
        web_dir: str | Path | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
        token: str | None = None,
    ):
        self.host = host
        self.port = int(port)
        self.db_path = str(db_path) if db_path is not None else None
        self.web_dir = resolve_web_dir(web_dir)
        self.token = token or secrets.token_urlsafe(24)
        self._state: AppState | None = None
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._error: str | None = None
        # True when this object only borrowed another instance's live server.
        # A borrower must never shut down a server it does not own.
        self._borrowed = False

    # ------------------------------------------------------------- lifecycle
    @property
    def is_running(self) -> bool:
        server = self._server
        return bool(server is not None and getattr(server, "started", False)
                    and self._thread is not None and self._thread.is_alive())

    @property
    def base_url(self) -> str:
        return f"http://{self.host}:{self.port}"

    @property
    def url(self) -> str:
        """Frontend URL with the bearer token attached for first load.

        The static client reads ``?token=`` once and stores it, so the
        QtWebEngine view (or a fallback browser tab) can authenticate without
        the user ever copying a secret.
        """
        return f"{self.base_url}/?token={self.token}"

    @property
    def last_error(self) -> str | None:
        return self._error

    def start(self, timeout: float = 30.0) -> str:
        """Start the server and block until it answers health checks.

        Returns the base URL. Raises ``RuntimeError`` with a human-readable
        message on failure — the desktop shell surfaces that text in the log
        window and in a tray notification instead of failing silently.
        """
        with EmbeddedServer._instance_lock:
            if self.is_running:
                return self.base_url
            existing = EmbeddedServer._instance
            if existing is not None and existing.is_running:
                # Reuse the live backend rather than binding a second one.
                self._server = existing._server
                self._thread = existing._thread
                self._state = existing._state
                self._borrowed = True
                self.port = existing.port
                self.token = existing.token
                return self.base_url

            import uvicorn

            try:
                self._state = AppState(db_path=self.db_path)
            except Exception as exc:  # database locked / unwritable directory
                self._error = f"Could not open the Tolesh database: {exc}"
                raise RuntimeError(self._error) from exc

            try:
                asgi = create_app(state=self._state, web_dir=self.web_dir,
                                  serve_static=True, api_token=self.token)
            except Exception as exc:
                self._state.close()
                self._state = None
                self._error = f"Could not build the Tolesh API: {exc}"
                raise RuntimeError(self._error) from exc

            config = uvicorn.Config(
                asgi,
                host=self.host,
                port=self.port,
                log_level="warning",
                access_log=False,
                lifespan="on",
                timeout_graceful_shutdown=3,
            )
            server = uvicorn.Server(config)
            thread = threading.Thread(target=server.run, name="tolesh-api", daemon=True)
            thread.start()

            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                if getattr(server, "started", False):
                    break
                if not thread.is_alive():
                    self._teardown()
                    self._error = (
                        "The Tolesh API thread exited during startup. "
                        "Check the log window for the traceback."
                    )
                    raise RuntimeError(self._error)
                time.sleep(0.05)
            else:
                self._teardown()
                self._error = f"The Tolesh API did not become ready within {timeout:.0f}s."
                raise RuntimeError(self._error)

            self._server = server
            self._thread = thread
            self.port = self._discover_port() or self.port
            if not self.port:
                self._teardown()
                self._error = "The Tolesh API started but reported no listening port."
                raise RuntimeError(self._error)

            if not self._wait_for_health(timeout=min(timeout, 10.0)):
                self._teardown()
                self._error = (
                    f"The Tolesh API is listening on {self.base_url} but "
                    "/api/health did not answer."
                )
                raise RuntimeError(self._error)

            EmbeddedServer._instance = self
            self._error = None
            return self.base_url

    def stop(self, timeout: float = 10.0) -> None:
        """Ask uvicorn to exit, join its thread, close the database.

        A handle that only *borrowed* the live server (see ``start``) detaches
        silently: shutting down a server owned by someone else would take the
        application's engine offline behind its owner's back.
        """
        if self._borrowed:
            self._server = None
            self._thread = None
            self._state = None
            self._borrowed = False
            return

        server = self._server
        if server is not None:
            try:
                server.should_exit = True
            except Exception:
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout)
        self._teardown()
        with EmbeddedServer._instance_lock:
            if EmbeddedServer._instance is self:
                EmbeddedServer._instance = None

    def _teardown(self) -> None:
        state, self._state = self._state, None
        self._server = None
        self._thread = None
        if state is not None:
            try:
                state.close()
            except Exception:
                pass

    # --------------------------------------------------------------- helpers
    def _discover_port(self) -> int | None:
        server = self._server
        for srv in getattr(server, "servers", []) or []:
            for sock in getattr(srv, "sockets", []) or []:
                try:
                    return int(sock.getsockname()[1])
                except OSError:
                    continue
        return None

    def _wait_for_health(self, timeout: float = 10.0) -> bool:
        url = f"{self.base_url}/api/health"
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(url, timeout=1.0) as resp:
                    if resp.status == 200:
                        json.loads(resp.read().decode("utf-8"))
                        return True
            except (urllib.error.URLError, OSError, ValueError):
                pass
            time.sleep(0.05)
        return False

    def health(self) -> dict[str, Any] | None:
        """Return the parsed health payload, or ``None`` when unreachable."""
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/health", timeout=2.0) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, OSError, ValueError):
            return None

    def __enter__(self) -> "EmbeddedServer":
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.stop()
