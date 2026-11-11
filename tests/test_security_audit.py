"""Security regressions for the audit.

Each test here corresponds to a weakness that was found in the running code and
fixed, so a future refactor that reopens it fails loudly instead of quietly.
The list:

  * the API bearer token was compared with ``!=``, which returns on the first
    differing byte and leaks the shared prefix to a caller who can time it;
  * ``ComputerTools.invoke(approved=...)`` trusted a caller-supplied dict, so an
    approval for one action could be spent on another;
  * a Telegram download had no size cap, so the remote end chose how much RAM
    the process would hold;
  * two allowlisted Telegram chats shared one memory store, so a second person
    could receive facts learned from the first.
"""

from __future__ import annotations

import inspect

import pytest
from fastapi.testclient import TestClient

from tolesh.channels import FakeTransport, TelegramChannel, TelegramLinks
from tolesh.channels import telegram as telegram_module
from tolesh.memory_ext import MemoryService
from tolesh.permissions import PermissionGate
from tolesh.server import create_app
from tolesh.server.app import AppState
from tolesh.system import SystemControls
from tolesh.tools_ext.computer import ComputerTools


class FakeShell:
    def __init__(self, code: int = 0, stdout: str = "", stderr: str = ""):
        self.code, self.stdout, self.stderr = code, stdout, stderr
        self.commands: list[list[str]] = []

    def __call__(self, command):
        self.commands.append(list(command))
        return self.code, self.stdout, self.stderr


def linux(**kwargs) -> SystemControls:
    return SystemControls(runner=kwargs.pop("runner", FakeShell()),
                          which=lambda name: f"/usr/bin/{name}",
                          system="linux", **kwargs)


# --------------------------------------------------------------- API token
def _client_with_token(token: str = "s3cret-token"):
    st = AppState(in_memory=True)
    app = create_app(state=st, serve_static=False, api_token=token)
    return TestClient(app), st


def test_a_request_without_the_bearer_token_is_refused():
    client, st = _client_with_token()
    try:
        assert client.get("/api/conversations").status_code == 401
    finally:
        st.close()


def test_a_wrong_bearer_token_is_refused():
    client, st = _client_with_token()
    try:
        bad = {"Authorization": "Bearer s3cret-tokeX"}
        assert client.get("/api/conversations", headers=bad).status_code == 401
        # A prefix of the real token must not be enough either.
        short = {"Authorization": "Bearer s3cret"}
        assert client.get("/api/conversations", headers=short).status_code == 401
    finally:
        st.close()


def test_the_right_bearer_token_is_accepted():
    client, st = _client_with_token()
    try:
        good = {"Authorization": "Bearer s3cret-token"}
        assert client.get("/api/conversations", headers=good).status_code == 200
    finally:
        st.close()


def test_health_stays_reachable_without_a_token():
    """A liveness probe that needs a credential cannot tell you it is alive."""
    client, st = _client_with_token()
    try:
        assert client.get("/api/health").status_code == 200
    finally:
        st.close()


def test_the_token_is_compared_in_constant_time():
    """``!=`` on a secret leaks its prefix length; ``compare_digest`` does not."""
    from tolesh.server import app as app_module

    source = inspect.getsource(app_module.create_app)
    assert "compare_digest" in source
    assert 'supplied != f"Bearer {api_token}"' not in source


# ------------------------------------------------------------ gate binding
def test_an_approval_cannot_be_forged_by_calling_the_tool_layer():
    """The old signature accepted ``{"status": "approved"}`` from any caller."""
    tools = ComputerTools(gate=PermissionGate(), controls=linux())
    forged = {"status": "approved", "tool": "write_text_file", "risk": "elevated"}

    result = tools.invoke("write_text_file",
                          {"path": "/tmp/forged.txt", "content": "x"},
                          approved=forged)

    assert result["status"] == "refused"
    assert "approval" in result["message"].lower()


def test_an_unknown_approval_id_is_refused():
    tools = ComputerTools(gate=PermissionGate(), controls=linux())

    result = tools.invoke("set_volume", {"level": 30}, approved="deadbeefdeadbeef")

    assert result["status"] == "refused"


def test_an_approval_cannot_be_widened_to_another_argument():
    """Approve a volume change of 10, then try to run 100 with the same id."""
    gate = PermissionGate()
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 10})
    assert pending["status"] == "pending-confirmation"
    gate.approve(pending["id"], pending["token"])

    widened = tools.invoke("set_volume", {"level": 100}, approved=pending["id"])

    assert widened["status"] == "refused"
    assert shell.commands == []


def test_an_approval_cannot_be_moved_to_another_tool():
    gate = PermissionGate()
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 10})
    gate.approve(pending["id"], pending["token"])

    moved = tools.invoke("set_brightness", {"level": 10}, approved=pending["id"])

    assert moved["status"] == "refused"
    assert shell.commands == []


def test_the_exact_approved_call_still_runs():
    gate = PermissionGate()
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 30})
    gate.approve(pending["id"], pending["token"])

    result = tools.invoke("set_volume", {"level": 30}, approved=pending["id"])

    assert result["status"] == "ok"
    assert shell.commands[0][:2] == ["pactl", "set-sink-volume"]


def test_a_pending_action_cannot_run_before_it_is_approved():
    gate = PermissionGate()
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 30})

    result = tools.invoke("set_volume", {"level": 30}, approved=pending["id"])

    assert result["status"] == "refused"
    assert shell.commands == []


def test_an_expired_approval_cannot_be_spent():
    gate = PermissionGate(ttl_sec=0.001)
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 30})
    import time

    time.sleep(0.02)

    assert gate.approve(pending["id"], pending["token"])["status"] == "error"
    assert tools.invoke("set_volume", {"level": 30},
                        approved=pending["id"])["status"] == "refused"
    assert shell.commands == []


def test_an_approval_cannot_be_replayed():
    gate = PermissionGate()
    shell = FakeShell()
    tools = ComputerTools(gate=gate, controls=linux(runner=shell))
    pending = tools.invoke("set_volume", {"level": 30})
    assert gate.approve(pending["id"], pending["token"])["status"] == "approved"

    assert gate.approve(pending["id"], pending["token"])["status"] == "error"
    assert tools.invoke("set_volume", {"level": 30},
                        approved=pending["id"])["status"] == "ok"
    # The second spend is refused: the record is no longer "approved" once used.
    gate._conn.execute("UPDATE permissions SET status='pending' WHERE id=?",
                       (pending["id"],))
    gate._conn.commit()
    assert tools.invoke("set_volume", {"level": 30},
                        approved=pending["id"])["status"] == "refused"


def test_a_wrong_token_does_not_approve():
    gate = PermissionGate()
    pending = gate.request("set_volume", {"level": 30})

    decision = gate.approve(pending["id"], "not-the-token")

    assert decision["status"] == "error"
    assert gate.status_of(pending["id"])["status"] == "pending"


def test_the_agent_layer_still_refuses_an_unapproved_control():
    """Belt and braces: the registry's own gate must not be bypassable either."""
    from tolesh.agents import WorkspaceContext, build_registry

    st = AppState(in_memory=True)
    try:
        registry = build_registry(WorkspaceContext(
            mcp=st.mcp, connectors=st.connectors, attachments=st.attachments,
            conversations=st.conversations, router=st.router, tools=st.tools))
        result = registry.invoke("set_volume", {"level": 30}, approved=False)
        assert result.ok is False
        assert "approval" in result.text.lower()
    finally:
        st.close()


# ------------------------------------------------------- telegram download
class _Response:
    def __init__(self, status_code=200, content=b"", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def json(self):
        return {}


class _Client:
    def __init__(self, response):
        self.response = response
        self.closed = False

    def get(self, url, timeout=None):
        return self.response

    def post(self, url, json=None, data=None, files=None, timeout=None):
        return self.response

    def close(self):
        self.closed = True


def test_a_download_larger_than_the_cap_is_refused():
    big = _Response(content=b"x" * (telegram_module.MAX_DOWNLOAD_BYTES + 1))
    transport = telegram_module.HttpTransport("123456789:" + "A" * 35,
                                              client=_Client(big))

    with pytest.raises(telegram_module.TelegramError):
        transport.download("voice/file_1.oga")


def test_a_declared_oversize_is_refused_before_the_body_is_kept():
    declared = _Response(content=b"", headers={
        "content-length": str(telegram_module.MAX_DOWNLOAD_BYTES + 1)})
    transport = telegram_module.HttpTransport("123456789:" + "A" * 35,
                                              client=_Client(declared))

    with pytest.raises(telegram_module.TelegramError):
        transport.download("voice/file_1.oga")


def test_an_ordinary_voice_note_is_returned_whole():
    body = b"OggS" + b"\x00" * 100
    transport = telegram_module.HttpTransport(
        "123456789:" + "A" * 35, client=_Client(_Response(content=body)))

    assert transport.download("voice/file_1.oga") == body


# --------------------------------------------------------- chat isolation
ALLOWED = 111


def _channel(tmp_path, *, allowlist=(ALLOWED,)):
    made: list[tuple[str, str]] = []

    def new_project(title: str) -> str:
        made.append(("project", title))
        return f"proj-{len(made)}"

    def new_conversation(title: str, project_id: str = "") -> str:
        made.append(("conversation", f"{title}|{project_id}"))
        return f"conv-{len(made)}"

    channel = TelegramChannel(
        FakeTransport(), links=TelegramLinks(str(tmp_path / "tg.db")),
        reply=lambda cid, text: f"answer to {text}",
        allowlist=lambda: set(allowlist),
        new_project=new_project, new_conversation=new_conversation)
    return channel, made


def test_each_chat_gets_its_own_project_and_conversation(tmp_path):
    channel, made = _channel(tmp_path, allowlist=(111, 222))

    channel.handle_update({"update_id": 1, "message": {"chat": {"id": 111}, "text": "a"}})
    channel.handle_update({"update_id": 2, "message": {"chat": {"id": 222}, "text": "b"}})

    projects = [value for kind, value in made if kind == "project"]
    assert projects == ["Telegram 111", "Telegram 222"]
    first, second = channel.links.conversation_for(111), channel.links.conversation_for(222)
    assert first and second and first != second
    assert channel.links.project_for(111) != channel.links.project_for(222)


def test_an_unauthorised_chat_never_creates_a_project(tmp_path):
    channel, made = _channel(tmp_path, allowlist=(ALLOWED,))

    channel.handle_update({"update_id": 1, "message": {"chat": {"id": 999}, "text": "hi"}})

    assert made == []
    assert channel.transport.sent == []
    assert channel.rejected == 1


def test_a_stringified_chat_id_is_still_checked_as_a_number(tmp_path):
    """A JSON payload is not trusted to be well-typed."""
    channel, _ = _channel(tmp_path, allowlist=(ALLOWED,))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": "111"}, "text": "hi"}})

    assert channel.messages == 1
    assert channel.transport.sent[0]["chat_id"] == 111


def test_a_boolean_is_not_a_chat_id(tmp_path):
    """``True`` is an int in Python; it must not match chat 1."""
    channel, _ = _channel(tmp_path, allowlist=(1,))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": True}, "text": "hi"}})

    assert channel.rejected == 0
    assert channel.transport.sent == []


def test_a_second_chat_cannot_see_the_first_chats_memories(tmp_path):
    """The end-to-end property: project-scoped memory is not shared."""
    st = AppState(in_memory=True)
    try:
        project_a = st.projects.create("Telegram 111")["id"]
        project_b = st.projects.create("Telegram 222")["id"]
        st.memory_service.store.add("personal", "Ada's bank PIN is 4321.",
                                    project_id=project_a)
        st.memory_service.store.add("personal", "Ada lives in Lagos.")

        seen_by_a = st.memory_service.context_for(
            "bank PIN", project_id=project_a, strict_project=True)
        seen_by_b = st.memory_service.context_for(
            "bank PIN", project_id=project_b, strict_project=True)

        assert "4321" in seen_by_a
        assert "4321" not in seen_by_b
        # A global memory is the owner's own fact and is hidden from a scoped
        # chat too, because "global" would otherwise mean "everyone".
        assert "Lagos" not in seen_by_b
    finally:
        st.close()


def test_the_shared_scope_still_sees_everything(tmp_path):
    """The owner can opt back into one memory across their own devices."""
    st = AppState(in_memory=True)
    try:
        project_a = st.projects.create("Telegram 111")["id"]
        st.memory_service.store.add("personal", "Ada lives in Lagos.")

        shared = st.memory_service.context_for("where does Ada live",
                                               project_id=project_a,
                                               strict_project=False)

        assert "Lagos" in shared
    finally:
        st.close()


def test_the_api_reports_and_stores_the_memory_scope():
    st = AppState(in_memory=True)
    app = create_app(state=st, serve_static=False)
    try:
        with TestClient(app) as client:
            assert client.get("/api/telegram").json()["memory_scope"] == "chat"
            client.patch("/api/telegram", json={"memory_scope": "shared"})
            assert client.get("/api/telegram").json()["memory_scope"] == "shared"
            assert client.patch("/api/telegram",
                                json={"memory_scope": "everyone"}).status_code == 422
    finally:
        st.close()


def test_an_old_chat_row_survives_the_project_column(tmp_path):
    """Additive migration: a table created before project scoping still opens."""
    import sqlite3

    path = str(tmp_path / "tg.db")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE telegram_chats (
        chat_id INTEGER PRIMARY KEY, conversation_id TEXT, title TEXT,
        created_at REAL, updated_at REAL)""")
    conn.execute("INSERT INTO telegram_chats VALUES (111,'conv-old','Telegram 111',1,1)")
    conn.commit()
    conn.close()

    links = TelegramLinks(path)

    assert links.conversation_for(111) == "conv-old"
    assert links.project_for(111) is None
    links.link(111, "conv-new", "Telegram 111", "proj-1")
    assert links.project_for(111) == "proj-1"


# --------------------------------------------------------- restart safety
def test_a_restart_does_not_leave_two_pollers(tmp_path):
    """``stop`` must join before ``start`` can build a replacement."""
    import threading
    import time

    class SlowTransport(FakeTransport):
        def __init__(self):
            super().__init__()
            self.live = 0
            self.max_live = 0
            self._lock = threading.Lock()

        def get_updates(self, offset, timeout):
            with self._lock:
                self.live += 1
                self.max_live = max(self.max_live, self.live)
            try:
                time.sleep(0.02)
                return []
            finally:
                with self._lock:
                    self.live -= 1

    transport = SlowTransport()
    channel = TelegramChannel(transport, links=TelegramLinks(str(tmp_path / "tg.db")),
                              reply=lambda cid, text: "ok", allowlist=lambda: {ALLOWED},
                              poll_timeout=0.2,
                              new_conversation=lambda t, p="": "conv-1")
    channel.start()
    channel.stop()
    channel.start()
    channel.stop()

    assert transport.max_live <= 1
    assert transport.closed is True


def test_stop_is_idempotent(tmp_path):
    channel = TelegramChannel(FakeTransport(),
                              links=TelegramLinks(str(tmp_path / "tg.db")),
                              reply=lambda cid, text: "ok",
                              allowlist=lambda: {ALLOWED},
                              new_conversation=lambda t, p="": "conv-1")
    channel.start()
    channel.stop()
    channel.stop()

    assert channel.running is False


# ------------------------------------------------- telegram turn wiring
class _Usage:
    total_tokens = 12

    def as_dict(self):
        return {"total_tokens": 12}


class _Result:
    text = "Noted."
    model_id = "stub/model"
    usage = _Usage()


def test_a_telegram_turn_carries_its_chat_project_into_the_prompt():
    """The isolation is only real if the server passes the project through."""
    st = AppState(in_memory=True)
    try:
        create_app(state=st, serve_static=False)
        project = st.projects.create("Telegram 111")["id"]
        conversation = st.conversations.create("Telegram 111", project_id=project)
        st.memory_service.store.add("personal", "Ada's bank PIN is 4321.",
                                    project_id=project)
        other = st.projects.create("Telegram 222")["id"]
        st.memory_service.store.add("personal", "Bob's bank PIN is 9999.",
                                    project_id=other)

        captured: dict[str, str] = {}

        def fake_complete(messages, **_kwargs):
            captured["system"] = messages[0]["content"]
            return _Result()

        st.router.complete = fake_complete
        st.telegram._reply(conversation["id"], "what is my bank PIN?")

        system = captured["system"]
        assert "4321" in system, "the chat's own memory was not retrieved"
        assert "9999" not in system, "another chat's memory leaked into the prompt"
    finally:
        st.close()
