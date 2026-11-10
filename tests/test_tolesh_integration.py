"""Integration tests for the unified Tolesh desktop application.

These cover the seam the project was missing: the PyQt6 shell and the FastAPI
engine must behave as **one** application that owns one persistent database.

What is tested here is the real lifecycle, not mocks:

* ``AppState`` resolves a file-backed database by default (the old default was
  ``:memory:``, which silently discarded every conversation on exit).
* ``EmbeddedServer`` binds a loopback port, serves the real static frontend,
  enforces the bearer token, and refuses to bind a second instance.
* Memory, conversations and provider settings survive a full stop/start cycle
  on the same file — the actual restart-persistence requirement.
* The tray's ``start_ai_backend`` / ``stop_ai_backend`` methods do what the
  desktop application calls at launch and at exit.

Hardware-dependent behaviour (microphone, speakers, a real desktop session)
is deliberately out of scope and is documented in ``TESTING.md`` instead of
being faked here.
"""

from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from tolesh.ai import registry as registry_module
from tolesh.server.app import AppState
from tolesh.server.embedded import EmbeddedServer, resolve_web_dir


# --------------------------------------------------------------------- helpers
def _request(server: EmbeddedServer, path: str, *, method: str = "GET",
             body: dict | None = None, auth: bool = True):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Content-Type": "application/json"}
    if auth:
        headers["Authorization"] = f"Bearer {server.token}"
    req = urllib.request.Request(
        server.base_url + path, data=data, method=method, headers=headers
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        payload = resp.read()
        return resp.status, payload


def _json(server: EmbeddedServer, path: str, **kwargs):
    _, payload = _request(server, path, **kwargs)
    return json.loads(payload.decode("utf-8"))


@pytest.fixture()
def isolated_home(tmp_path, monkeypatch):
    """Point every persistent store at a throwaway directory.

    ``TOLESH_DATA_DIR`` drives the shared database path, and the registry's
    per-user providers file is redirected at the module attribute it actually
    reads, so a test can never touch a developer's real configuration.
    """
    data_dir = tmp_path / "data"
    monkeypatch.setenv("TOLESH_DATA_DIR", str(data_dir))
    monkeypatch.delenv("TOLESH_DB_PATH", raising=False)
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    providers = tmp_path / "providers.json"
    monkeypatch.setenv("TOLESH_PROVIDERS_PATH", str(providers))
    monkeypatch.setattr(registry_module, "CONFIG_PATH", providers)
    return tmp_path


@pytest.fixture()
def running_server(isolated_home):
    server = EmbeddedServer(web_dir=resolve_web_dir())
    server.start(timeout=25)
    try:
        yield server
    finally:
        server.stop()


# ------------------------------------------------------- persistent by default
def test_appstate_defaults_to_file_backed_database(isolated_home):
    state = AppState()
    try:
        assert state.db_path != ":memory:"
        assert Path(state.db_path).name == "tolesh.db"
        assert Path(state.db_path).parent.is_dir()
        # The file itself must exist once a store has written its schema.
        assert Path(state.db_path).exists()
    finally:
        state.close()


def test_data_directory_honours_override(isolated_home):
    from tolesh import _sqlite

    assert Path(_sqlite.default_data_dir()) == isolated_home / "data"
    assert _sqlite.default_db_path() == str(isolated_home / "data" / "tolesh.db")


# ------------------------------------------------------------- embedded server
def test_embedded_server_serves_real_frontend(running_server):
    status, payload = _request(running_server, "/api/health", auth=False)
    health = json.loads(payload)
    assert status == 200
    assert health["status"] == "ok"
    assert health["product"] == "Tolesh"

    # The static client, not a stub page.
    status, html = _request(running_server, "/", auth=False)
    text = html.decode("utf-8")
    assert status == 200
    assert 'id="composer"' in text
    assert "Tolesh" in text


def test_embedded_server_binds_an_ephemeral_loopback_port(running_server):
    assert running_server.host == "127.0.0.1"
    assert running_server.port > 0
    with socket.create_connection(("127.0.0.1", running_server.port), timeout=3):
        pass


def test_token_guard_rejects_unauthenticated_api_calls(running_server):
    with pytest.raises(urllib.error.HTTPError) as excinfo:
        _request(running_server, "/api/providers", auth=False)
    assert excinfo.value.code == 401

    # Health stays open so the shell can probe readiness before it has a token.
    assert _request(running_server, "/api/health", auth=False)[0] == 200
    assert _request(running_server, "/api/providers")[0] == 200


def test_frontend_url_carries_the_token(running_server):
    assert running_server.url.endswith(f"/?token={running_server.token}")


def test_second_server_reuses_the_first_instead_of_double_binding(running_server):
    other = EmbeddedServer(web_dir=resolve_web_dir())
    other.start(timeout=10)
    try:
        assert other.port == running_server.port
        assert other.token == running_server.token
    finally:
        other.stop()
    # Stopping the borrower must not take the owner's port down.
    assert running_server.is_running


# ------------------------------------------------------- restart persistence
def test_memory_and_conversations_survive_restart(isolated_home):
    first = EmbeddedServer(web_dir=resolve_web_dir())
    first.start(timeout=25)
    try:
        _json(first, "/api/memory", method="POST",
              body={"category": "preference",
                    "content": "Prefers Pidgin greetings in the morning"})
        chat = _json(first, "/api/chat/sync", method="POST",
                     body={"message": "How far"})
        conversation_id = chat["conversation_id"]
        assert conversation_id
    finally:
        first.stop()

    # A brand-new server object, same data directory, same file.
    second = EmbeddedServer(web_dir=resolve_web_dir())
    second.start(timeout=25)
    try:
        memories = _json(second, "/api/memory")["items"]
        assert any("Pidgin" in item["content"] for item in memories)

        conversations = _json(second, "/api/conversations")["conversations"]
        assert any(c["id"] == conversation_id for c in conversations)

        messages = _json(second, f"/api/conversations/{conversation_id}/messages")
        roles = [m["role"] for m in messages["messages"]]
        assert "user" in roles
        assert "assistant" in roles
    finally:
        second.stop()


def test_provider_settings_persist_and_stay_private(isolated_home):
    server = EmbeddedServer(web_dir=resolve_web_dir())
    server.start(timeout=25)
    try:
        result = _json(server, "/api/providers/openai", method="PATCH",
                       body={"api_key": "sk-integration-test", "enabled": True})
        assert result["provider"]["configured"] is True
        # Redacted means the secret is absent, whatever mask the registry uses.
        assert "sk-integration-test" not in json.dumps(result)

        # The secret is on disk, private to the user, and nowhere in responses.
        raw = json.loads((isolated_home / "providers.json").read_text("utf-8"))
        assert raw["providers"]["openai"]["api_key"] == "sk-integration-test"
        mode = (isolated_home / "providers.json").stat().st_mode & 0o777
        assert mode == 0o600

        providers = _json(server, "/api/providers")["providers"]
        openai = next(p for p in providers if p["name"] == "openai")
        assert "sk-integration-test" not in json.dumps(openai)
    finally:
        server.stop()

    # Reload from disk: a new process must see the saved key.
    reopened = EmbeddedServer(web_dir=resolve_web_dir())
    reopened.start(timeout=25)
    try:
        providers = _json(reopened, "/api/providers")["providers"]
        openai = next(p for p in providers if p["name"] == "openai")
        assert openai["configured"] is True
    finally:
        reopened.stop()


def test_default_model_selection_validates_and_persists(isolated_home):
    server = EmbeddedServer(web_dir=resolve_web_dir())
    server.start(timeout=25)
    try:
        cfg = _json(server, "/api/config", method="PATCH",
                    body={"default_model": "openai:gpt-4o"})
        assert cfg["default_model"] == "openai:gpt-4o"

        with pytest.raises(urllib.error.HTTPError) as excinfo:
            _json(server, "/api/config", method="PATCH",
                  body={"default_model": "nope:does-not-exist"})
        assert excinfo.value.code == 400

        # Clearing restores automatic routing.
        cfg = _json(server, "/api/config", method="PATCH", body={"default_model": ""})
        assert cfg["default_model"] in (None, "")
    finally:
        server.stop()

    saved = json.loads((isolated_home / "providers.json").read_text("utf-8"))
    assert "default_model" in saved


def test_provider_test_endpoint_is_honest_without_credentials(running_server):
    result = _json(running_server, "/api/providers/openai/test", method="POST")
    # No credentials in the test environment: it must say so, not claim success.
    assert result["ok"] is False
    assert result["status"] in {"unconfigured", "fallback", "error", "no_models"}


def test_consequential_tools_require_approval_and_stay_refused(running_server):
    # Step 1: the gate intercepts. Nothing runs without a human decision.
    pending = _json(running_server, "/api/tools/invoke", method="POST",
                    body={"tool": "run_command", "args": {"command": "echo hi"}})
    assert pending["status"] == "pending-confirmation"
    assert pending["id"] and pending["token"]

    # Step 2: even after explicit approval, the executor refuses the action.
    approved = _json(running_server, f"/api/permissions/{pending['id']}/approve",
                     method="POST",
                     body={"token": pending["token"], "execute": True})
    assert approved["execution"]["status"] == "refused"

    # The audit trail recorded both the request and the decision.
    audit = _json(running_server, "/api/permissions/audit")["entries"]
    assert any(entry["tool"] == "run_command" for entry in audit)


# -------------------------------------------------- desktop lifecycle seams
class _LogSignal:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def emit(self, text: str) -> None:  # pragma: no cover - trivial
        self.lines.append(text)


class _Signals:
    def __init__(self) -> None:
        self.new_log = _LogSignal()


class _TrayIcon:
    def __init__(self) -> None:
        self.messages: list[tuple] = []

    def showMessage(self, *args, **kwargs) -> None:  # noqa: N802 (Qt name)
        self.messages.append(args)


class _TrayHost:
    """The attributes ``ToleshSystemTray`` lifecycle methods actually touch."""

    def __init__(self) -> None:
        self.ai_server = None
        self.ai_window = None
        self._ai_error = None
        self.log_signals = _Signals()
        self.tray_icon = _TrayIcon()


def test_tray_starts_and_stops_the_embedded_backend(isolated_home, qapp):
    from desktop_app.app import ToleshSystemTray

    host = _TrayHost()
    assert ToleshSystemTray.start_ai_backend(host, timeout=25) is True
    assert host.ai_server is not None
    assert host.ai_server.is_running
    assert any("AI engine ready" in line for line in host.log_signals.new_log.lines)

    port = host.ai_server.port
    ToleshSystemTray.stop_ai_backend(host)
    assert host.ai_server is None

    # The port is released, not merely forgotten.
    with pytest.raises(OSError):
        with socket.create_connection(("127.0.0.1", port), timeout=1):
            pass


def test_tray_reports_startup_failure_instead_of_raising(isolated_home, qapp,
                                                        monkeypatch):
    from desktop_app.app import ToleshSystemTray

    host = _TrayHost()

    def explode(self, timeout=30.0):
        raise RuntimeError("simulated database failure")

    monkeypatch.setattr(EmbeddedServer, "start", explode)
    assert ToleshSystemTray.start_ai_backend(host, timeout=1) is False
    assert "simulated database failure" in (host._ai_error or "")
    assert host.tray_icon.messages, "the user must be told the engine failed"
    assert any("failed to start" in line for line in host.log_signals.new_log.lines)


def test_ai_window_builds_without_webengine(isolated_home, qapp, monkeypatch):
    from desktop_app import ai_window

    monkeypatch.setattr(ai_window, "HAS_WEBENGINE", False)
    window = ai_window.ToleshAIWindow("http://127.0.0.1:9999/?token=abc")
    try:
        assert window.embedded_available() is False
        assert window.web_view is None
        assert "127.0.0.1:9999" in window._address.text()
        window.show_error("boom")
        assert "boom" in window._address.text()
    finally:
        window.close()


def test_ai_window_retry_uses_the_reconnect_callback(isolated_home, qapp,
                                                    monkeypatch):
    from desktop_app import ai_window

    monkeypatch.setattr(ai_window, "HAS_WEBENGINE", False)
    calls: list[int] = []

    def reconnect() -> str:
        calls.append(1)
        return "http://127.0.0.1:10000/?token=def"

    window = ai_window.ToleshAIWindow("http://127.0.0.1:9999/?token=abc",
                                      on_retry=reconnect)
    try:
        window.retry()
        assert calls == [1]
        assert "10000" in window._address.text()
    finally:
        window.close()
