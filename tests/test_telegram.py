"""Telegram as a second way in: dispatch, lifecycle, token handling, API.

Everything here runs against ``FakeTransport``, so the suite never opens a
socket and never needs a bot token. The one end-to-end test injects the fake
into the running application and checks that a message arriving "from Telegram"
produces a real answer through the same chat pipeline the window uses.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time

import pytest
from fastapi.testclient import TestClient

from tolesh.ai import ModelRouter, Registry
from tolesh.channels import (BOT_CONNECTION_ID, FakeTransport, TelegramChannel,
                             TelegramError, TelegramLinks, TelegramService,
                             TelegramSettings, looks_like_token)
from tolesh.server import create_app
from tolesh.server.app import AppState

ALLOWED = 4242


def wait_until(predicate, timeout: float = 3.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def message(chat_id=ALLOWED, text="hello", update_id=1, **extra):
    payload = {"update_id": update_id, "message": {"chat": {"id": chat_id},
                                                   "text": text}}
    payload["message"].update(extra)
    return payload


class Recorder:
    """A reply callable that records what it was asked and answers predictably."""

    def __init__(self, answer="An answer.", *, error: Exception | None = None):
        self.answer = answer
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def __call__(self, conversation_id: str, text: str) -> str:
        self.calls.append((conversation_id, text))
        if self.error is not None:
            raise self.error
        return self.answer


def make_channel(tmp_path, *, allowlist=(ALLOWED,), reply=None,
                 transport=None, transcribe=None, synthesize=None,
                 new_conversation=None, links=None):
    links = links or TelegramLinks(str(tmp_path / "tg.db"))
    channel = TelegramChannel(
        transport or FakeTransport(), links=links,
        reply=reply or Recorder(), allowlist=lambda: set(allowlist),
        transcribe=transcribe, synthesize=synthesize,
        new_conversation=new_conversation or (
            lambda title, project_id="": f"conv-{title}"))
    return channel


# ------------------------------------------------------------------ dispatch
def test_an_allowed_chat_gets_an_answer(tmp_path):
    reply = Recorder("Paris.")
    channel = make_channel(tmp_path, reply=reply)

    channel.handle_update(message(text="Capital of France?"))

    assert reply.calls == [(f"conv-Telegram {ALLOWED}", "Capital of France?")]
    assert channel.transport.sent == [{"chat_id": ALLOWED, "text": "Paris."}]
    assert (channel.messages, channel.replies) == (1, 1)


def test_a_stranger_is_ignored_without_a_reply(tmp_path):
    reply = Recorder()
    channel = make_channel(tmp_path, reply=reply)

    channel.handle_update(message(chat_id=999))

    assert reply.calls == []                     # never reached the pipeline
    assert channel.transport.sent == []          # and never told the bot exists
    assert channel.rejected == 1
    assert channel.last_rejected_chat == 999
    assert channel.links.conversation_for(999) is None


def test_a_string_chat_id_is_the_same_chat(tmp_path):
    reply = Recorder()
    channel = make_channel(tmp_path, reply=reply)

    channel.handle_update(message(chat_id=str(ALLOWED)))

    assert reply.calls, "a numeric string must match the integer allowlist"
    assert channel.rejected == 0


def test_a_non_numeric_chat_id_is_dropped(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update({"update_id": 1, "message": {"chat": {"id": "abc"},
                                                       "text": "hi"}})
    channel.handle_update({"update_id": 2, "message": {}})

    assert channel.messages == 0
    assert channel.transport.sent == []


def test_a_bool_chat_id_is_not_accepted_as_an_integer(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update({"update_id": 1, "message": {"chat": {"id": True},
                                                       "text": "hi"}})

    assert channel.messages == 0


def test_two_chats_never_share_a_conversation(tmp_path):
    reply = Recorder()
    channel = make_channel(tmp_path, allowlist=(1, 2), reply=reply)

    channel.handle_update(message(chat_id=1, text="first"))
    channel.handle_update(message(chat_id=2, text="second", update_id=2))

    first, second = (call[0] for call in reply.calls)
    assert first != second
    assert channel.links.conversation_for(1) != channel.links.conversation_for(2)


def test_the_same_chat_reuses_its_conversation(tmp_path):
    reply = Recorder()
    channel = make_channel(tmp_path, reply=reply)

    channel.handle_update(message(text="one"))
    channel.handle_update(message(text="two", update_id=2))

    assert reply.calls[0][0] == reply.calls[1][0]


def test_new_starts_a_fresh_conversation(tmp_path):
    reply = Recorder()
    made: list[str] = []

    def new_conversation(title: str, project_id: str = "") -> str:
        made.append(f"conv-{len(made) + 1}")
        return made[-1]

    channel = make_channel(tmp_path, reply=reply, new_conversation=new_conversation)

    channel.handle_update(message(text="one"))
    channel.handle_update(message(text="/new", update_id=2))
    channel.handle_update(message(text="two", update_id=3))

    assert reply.calls[0][0] == "conv-1"
    assert reply.calls[-1][0] == "conv-2"
    assert "Started a new conversation" in channel.transport.sent[1]["text"]


def test_id_reports_the_chat_id(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(text="/id"))

    assert channel.transport.sent[0]["text"] == f"This chat's id is {ALLOWED}."


def test_help_explains_what_the_bot_does(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(text="/help"))

    assert "/new" in channel.transport.sent[0]["text"]


def test_an_unknown_command_is_reported(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(text="/dance"))

    assert "Unknown command" in channel.transport.sent[0]["text"]


def test_a_command_with_a_bot_suffix_is_recognised(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(text="/id@tolesh_bot"))

    assert channel.transport.sent[0]["text"].startswith("This chat's id")


def test_a_message_with_no_text_says_what_it_can_read(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update({"update_id": 1, "message": {"chat": {"id": ALLOWED}}})

    assert "text and voice" in channel.transport.sent[0]["text"]


def test_the_update_offset_advances(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(update_id=41))

    assert channel._offset == 42


# --------------------------------------------------------------------- voice
def test_a_voice_note_is_transcribed_answered_and_spoken(tmp_path):
    seen: list[bytes] = []

    def transcribe(audio: bytes) -> str:
        seen.append(audio)
        return "what is the weather"

    channel = make_channel(
        tmp_path, transcribe=transcribe,
        synthesize=lambda text: (b"OGG-DATA", "audio/ogg"))

    channel.handle_update({
        "update_id": 1,
        "message": {"chat": {"id": ALLOWED},
                    "voice": {"file_id": "abc", "duration": 3}}})

    assert seen == [b"OggS-fake-audio"]
    sent = channel.transport.audio[0]
    assert sent["voice"] is True
    assert sent["mime"] == "audio/ogg"
    assert sent["filename"] == "reply.ogg"
    assert channel.replies == 1


def test_a_wav_reply_is_sent_as_a_file_not_a_voice_note(tmp_path):
    channel = make_channel(
        tmp_path, transcribe=lambda audio: "hello",
        synthesize=lambda text: (b"WAV", "audio/wav"))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert channel.transport.audio[0]["voice"] is False


def test_voice_without_speech_support_explains_itself(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert "speech-to-text" in channel.transport.sent[0]["text"]
    assert channel.transport.audio == []


def test_a_failed_transcription_is_reported(tmp_path):
    def boom(audio):
        raise RuntimeError("whisper is not installed")

    channel = make_channel(tmp_path, transcribe=boom,
                           synthesize=lambda text: (b"x", "audio/ogg"))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert "could not transcribe" in channel.transport.sent[0]["text"]


def test_an_empty_transcription_is_reported(tmp_path):
    channel = make_channel(tmp_path, transcribe=lambda audio: "   ",
                           synthesize=lambda text: (b"x", "audio/ogg"))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert "could not make out" in channel.transport.sent[0]["text"]


def test_a_failed_download_is_reported(tmp_path):
    class BrokenDownload(FakeTransport):
        def download(self, file_path):
            raise TelegramError("Download failed (404).")

    channel = make_channel(tmp_path, transport=BrokenDownload(),
                           transcribe=lambda audio: "hi",
                           synthesize=lambda text: (b"x", "audio/ogg"))

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert "could not fetch" in channel.transport.sent[0]["text"]


def test_a_failed_synthesis_still_delivers_the_answer_as_text(tmp_path):
    def broken_tts(text):
        raise RuntimeError("piper is missing")

    channel = make_channel(tmp_path, transcribe=lambda audio: "hello",
                           synthesize=broken_tts)

    channel.handle_update({"update_id": 1,
                           "message": {"chat": {"id": ALLOWED},
                                       "voice": {"file_id": "abc"}}})

    assert channel.transport.audio == []
    assert channel.transport.sent[0]["text"] == "An answer."
    assert "synthesis failed" in channel.last_error


# ------------------------------------------------------------------- failures
def test_a_pipeline_error_is_answered_not_swallowed(tmp_path):
    channel = make_channel(tmp_path, reply=Recorder(error=RuntimeError("no model")))

    channel.handle_update(message(text="hello"))

    assert "no model" in channel.transport.sent[0]["text"]
    assert channel.replies == 0
    assert "RuntimeError" in channel.last_error


def test_an_empty_answer_is_replaced_with_a_notice(tmp_path):
    channel = make_channel(tmp_path, reply=Recorder(answer=""))

    channel.handle_update(message(text="hello"))

    assert channel.transport.sent[0]["text"] == "I did not get an answer from the model."


def test_a_failing_conversation_factory_is_reported(tmp_path):
    def no_conversation(title, project_id=""):
        raise RuntimeError("database is closed")

    channel = make_channel(tmp_path, new_conversation=no_conversation)

    channel.handle_update(message(text="hello"))

    assert "database is closed" in channel.transport.sent[0]["text"]


# ------------------------------------------------------------------ lifecycle
def test_start_runs_a_thread_and_stop_joins_it(tmp_path):
    transport = FakeTransport()
    channel = make_channel(tmp_path, transport=transport)

    channel.start()
    assert channel.running is True
    channel.stop()

    assert channel.running is False
    assert transport.closed is True


def test_polling_recovers_from_a_network_error(tmp_path):
    reply = Recorder()
    transport = FakeTransport(updates=[[message(text="after the storm")]])
    transport.failures = 2
    channel = make_channel(tmp_path, transport=transport, reply=reply)

    channel.start()
    try:
        assert wait_until(lambda: reply.calls, timeout=8.0), \
            "the poller gave up instead of retrying"
    finally:
        channel.stop()

    assert transport.offsets[0] is None
    assert reply.calls[0][1] == "after the storm"


def test_status_reports_what_the_ui_needs(tmp_path):
    channel = make_channel(tmp_path)

    channel.handle_update(message(chat_id=999))

    status = channel.status()
    assert status["running"] is False
    assert status["rejected"] == 1
    assert status["last_rejected_chat"] == 999
    assert status["messages"] == 0


# -------------------------------------------------------------------- service
def test_the_service_refuses_to_start_without_a_token(tmp_path):
    service = TelegramService(links=TelegramLinks(str(tmp_path / "tg.db")),
                              reply=Recorder(), allowlist=lambda: set(),
                              token=lambda: "")

    status = service.start()

    assert status["running"] is False
    assert "token" in status["last_error"].lower()


def test_the_service_records_who_it_connected_as(tmp_path):
    service = TelegramService(
        links=TelegramLinks(str(tmp_path / "tg.db")), reply=Recorder(),
        allowlist=lambda: {ALLOWED},
        token=lambda: "123456789:" + "A" * 35,
        transport_factory=lambda token: FakeTransport())

    status = service.start()
    try:
        assert status["running"] is True
        assert status["bot"] == "tolesh_test_bot"
    finally:
        service.stop()


def test_start_if_configured_does_nothing_when_switched_off(tmp_path):
    service = TelegramService(
        links=TelegramLinks(str(tmp_path / "tg.db")), reply=Recorder(),
        allowlist=lambda: set(), token=lambda: "123456789:" + "A" * 35,
        transport_factory=lambda token: FakeTransport())

    status = service.start_if_configured(enabled=False)

    assert status["running"] is False
    assert status["last_error"] == ""


def test_start_if_configured_explains_a_missing_token(tmp_path):
    service = TelegramService(links=TelegramLinks(str(tmp_path / "tg.db")),
                              reply=Recorder(), allowlist=lambda: set(),
                              token=lambda: "")

    status = service.start_if_configured(enabled=True)

    assert status["running"] is False
    assert "no bot token" in status["last_error"].lower()


def test_a_rejected_token_is_a_reported_state_not_a_crash(tmp_path):
    def bad_me():
        raise TelegramError("The bot token was rejected by Telegram.")

    class Rejected(FakeTransport):
        def me(self):
            return bad_me()

    service = TelegramService(
        links=TelegramLinks(str(tmp_path / "tg.db")), reply=Recorder(),
        allowlist=lambda: set(), token=lambda: "1:" + "B" * 35,
        transport_factory=lambda token: Rejected())

    status = service.start()

    assert status["running"] is False
    assert "rejected" in status["last_error"]


# --------------------------------------------------------------------- token
@pytest.mark.parametrize("token,ok", [
    ("123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw", True),
    ("1:" + "x" * 30, True),
    ("nonsense", False),
    ("abc:def", False),
    ("", False),
    ("123456789:", False),
    ("123456789:has space inside the secret", False),
])
def test_a_token_is_recognised_by_its_shape(token, ok):
    assert looks_like_token(token) is ok


# ------------------------------------------------------------------ settings
def test_settings_default_to_off_with_nobody_allowed(tmp_path):
    settings = TelegramSettings(str(tmp_path / "tg.db"))

    values = settings.get()

    assert values["enabled"] is False
    assert values["allowlist"] == []
    # Isolation is the default: a second allowlisted chat must not inherit the
    # first one's memories unless the owner opts in.
    assert values["memory_scope"] == "chat"


def test_settings_survive_a_new_instance(tmp_path):
    path = str(tmp_path / "tg.db")
    first = TelegramSettings(path)
    first.set_enabled(True)
    first.add_chat(7)
    first.add_chat(7)
    first.close()

    second = TelegramSettings(path)

    values = second.get()

    assert values["enabled"] is True
    assert values["allowlist"] == [7]
    assert values["memory_scope"] == "chat"


def test_removing_a_chat_leaves_the_others(tmp_path):
    settings = TelegramSettings(str(tmp_path / "tg.db"))
    settings.set_allowlist([1, 2, 3])

    assert settings.remove_chat(2) == [1, 3]
    assert settings.remove_chat(99) == [1, 3]


def test_a_damaged_allowlist_reads_as_empty(tmp_path):
    path = str(tmp_path / "tg.db")
    settings = TelegramSettings(path)
    settings._put("allowlist", "not-a-list")

    assert settings.get()["allowlist"] == []


# ----------------------------------------------------------------------- API
@pytest.fixture
def api(tmp_path, monkeypatch):
    """A live app with an offline router and a fake Telegram behind it."""
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)
    state = AppState(in_memory=True)
    offline = Registry.from_defaults().provider("offline")
    state.registry = Registry({"offline": offline})
    state.router = ModelRouter(state.registry, state.ledger, offline_delay=0)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    transports: list[FakeTransport] = []

    def factory(token):
        transport = FakeTransport()
        transports.append(transport)
        return transport

    state.telegram.set_transport_factory(factory)
    with TestClient(app) as client:
        yield client, state, transports
    state.close()


def test_the_api_starts_switched_off(api):
    client, _state, _t = api

    payload = client.get("/api/telegram").json()

    assert payload["available"] is True
    assert payload["enabled"] is False
    assert payload["has_token"] is False
    assert payload["allowlist"] == []
    assert payload["status"]["running"] is False


def test_the_api_refuses_a_malformed_token(api):
    client, _state, _t = api

    response = client.patch("/api/telegram", json={"token": "oops"})

    assert response.status_code == 400
    assert "BotFather" in response.json()["detail"]


def test_the_api_stores_the_token_encrypted(api):
    client, state, _t = api
    token = "123456789:" + "S" * 35

    response = client.patch("/api/telegram", json={"token": token})

    assert response.status_code == 200
    assert response.json()["has_token"] is True
    assert token not in json.dumps(response.json())
    # It is readable by the connector layer…
    assert state.connectors.store.secrets(BOT_CONNECTION_ID)["access_token"] == token
    # …and unreadable in the file itself.
    raw = sqlite3.connect(state.db_path, uri=True).execute(
        "SELECT access_token FROM connections WHERE id=?", (BOT_CONNECTION_ID,)
    ).fetchone()[0]
    assert token not in raw


def test_the_api_manages_the_allowlist(api):
    client, _state, _t = api

    assert client.patch("/api/telegram", json={"add_chat": 11}).json()["allowlist"] == [11]
    assert client.patch("/api/telegram", json={"add_chat": 22}).json()["allowlist"] == [11, 22]
    assert client.patch("/api/telegram", json={"remove_chat": 11}).json()["allowlist"] == [22]
    assert client.patch("/api/telegram", json={"allowlist": [5, 6]}).json()["allowlist"] == [5, 6]


def test_the_api_will_not_start_without_a_token(api):
    client, _state, _t = api

    response = client.post("/api/telegram/start")

    assert response.status_code == 400
    assert "token" in response.json()["detail"].lower()


def test_the_api_starts_and_stops_the_poller(api):
    client, _state, transports = api
    client.patch("/api/telegram", json={"token": "123456789:" + "T" * 35,
                                        "add_chat": ALLOWED})

    started = client.patch("/api/telegram", json={"enabled": True}).json()
    try:
        assert started["enabled"] is True
        assert started["status"]["running"] is True
        assert started["status"]["bot"] == "tolesh_test_bot"
        assert transports, "the configured transport was never built"
    finally:
        stopped = client.post("/api/telegram/stop").json()

    assert stopped["enabled"] is False
    assert stopped["status"]["running"] is False


def test_the_api_forgets_a_token(api):
    client, state, _t = api
    client.patch("/api/telegram", json={"token": "123456789:" + "U" * 35,
                                        "enabled": False})

    payload = client.delete("/api/telegram/token").json()

    assert payload["has_token"] is False
    assert state.connectors.store.secrets(BOT_CONNECTION_ID) is None


def test_the_api_tests_the_token_without_polling(api):
    client, _state, _t = api
    client.patch("/api/telegram", json={"token": "123456789:" + "V" * 35})

    payload = client.post("/api/telegram/test").json()

    assert payload["ok"] is True
    assert payload["bot"] == "tolesh_test_bot"


def test_the_api_reports_a_missing_token_on_test(api):
    client, _state, _t = api

    payload = client.post("/api/telegram/test").json()

    assert payload["ok"] is False
    assert "token" in payload["error"].lower()


def test_the_api_says_nobody_is_allowed_yet(api):
    client, _state, _t = api
    client.patch("/api/telegram", json={"token": "123456789:" + "W" * 35})
    client.patch("/api/telegram", json={"enabled": True})
    try:
        payload = client.get("/api/telegram").json()
    finally:
        client.post("/api/telegram/stop")

    assert "No chat is allowed" in payload["note"]


def test_a_message_through_the_api_reaches_the_chat_pipeline(api):
    """End to end: a Telegram message becomes a stored exchange."""
    client, state, transports = api
    client.patch("/api/telegram", json={"token": "123456789:" + "X" * 35,
                                        "add_chat": ALLOWED})
    client.patch("/api/telegram", json={"enabled": True})
    transport = transports[0]
    transport.queues.append([message(text="Say hello.")])
    try:
        assert wait_until(lambda: transport.sent, timeout=10.0), \
            "no reply was sent for the message"
    finally:
        client.post("/api/telegram/stop")

    sent = transport.sent[0]
    assert sent["chat_id"] == ALLOWED
    assert sent["text"].strip()
    conversation_id = state.telegram_links.conversation_for(ALLOWED)
    assert conversation_id
    roles = [m["role"] for m in state.conversations.messages(conversation_id)]
    assert roles == ["user", "assistant"]


def test_a_stranger_through_the_api_creates_nothing(api):
    client, state, transports = api
    client.patch("/api/telegram", json={"token": "123456789:" + "Y" * 35,
                                        "add_chat": ALLOWED})
    client.patch("/api/telegram", json={"enabled": True})
    transport = transports[0]
    transport.queues.append([message(chat_id=666, text="let me in")])
    try:
        assert wait_until(lambda: transport.offsets and transport.offsets[-1] is not None
                          or transport.sent, timeout=5.0)
        time.sleep(0.2)
    finally:
        client.post("/api/telegram/stop")

    assert transport.sent == []
    assert state.telegram_links.conversation_for(666) is None
    assert state.conversations.list() == []


def test_the_capability_report_includes_telegram(api):
    client, _state, _t = api

    phases = client.get("/api/capabilities").json()["phases"]

    assert phases["telegram"]["available"] is True
