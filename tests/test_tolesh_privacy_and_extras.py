"""Phase 12 and the extra features: settings, audit, export/deletion,
assistants, templates, share links, message editing and branching.
"""

from __future__ import annotations

import io
import json
import zipfile
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from tolesh.ai.router import RouterEvent
from tolesh.server import create_app
from tolesh.server.app import AppState, _messages_for
from tolesh.server.schemas import ChatRequest


class FakeStreamRouter:
    """Streams a fixed answer, for regenerate tests."""

    def __init__(self, text: str = "A fresh answer."):
        self.text = text
        self.calls: list[list[dict]] = []

    def select(self, task="chat", preferred=None):
        return SimpleNamespace(label="fake", id="fake")

    def stream(self, messages, **kwargs):
        self.calls.append([dict(m) for m in messages])
        yield RouterEvent("token", self.text)
        yield RouterEvent("done", None, model_id="fake-model")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


# ------------------------------------------------------------------ settings
def test_settings_defaults_and_validation(client):
    values = client.get("/api/settings").json()["values"]
    assert values["share_links_enabled"] is False
    assert values["telemetry"] is False

    ok = client.patch("/api/settings", json={"agent_max_steps": 12})
    assert ok.status_code == 200
    assert ok.json()["values"]["agent_max_steps"] == 12

    assert client.patch("/api/settings", json={"unknown": 1}).status_code == 400
    assert client.patch("/api/settings", json={"agent_max_steps": 99}).status_code == 400
    assert client.patch("/api/settings", json={"agent_max_steps": "many"}).status_code == 400
    assert client.patch("/api/settings", json={"telemetry": True}).status_code == 400


def test_settings_can_be_reset(client):
    client.patch("/api/settings", json={"agent_max_steps": 3})
    reset = client.post("/api/settings/reset").json()["values"]
    assert reset["agent_max_steps"] == 8


# --------------------------------------------------------------------- audit
def test_audit_records_a_denied_action(client):
    pending = client.post("/api/tools/invoke", json={
        "tool": "delete_file", "args": {"path": "/tmp/x"}, "reason": "test"}).json()
    assert pending["status"] == "pending-confirmation"
    decision = pending
    client.post(f"/api/permissions/{decision['id']}/deny",
                json={"token": decision["token"], "execute": False})
    events = client.get("/api/audit").json()["events"]
    assert any(e["kind"] == "permission" and e["action"] == "delete_file"
               for e in events)
    assert client.get("/api/audit/summary").json()["total"] >= 1


# ------------------------------------------------------------ export / delete
def test_export_is_a_zip_with_content_and_no_credentials(client):
    cid = client.post("/api/conversations", json={"title": "Exported"}).json()["id"]
    client.post("/api/chat/sync", json={"message": "hello", "conversation_id": cid})
    client.post("/api/memory", json={"category": "preference", "content": "likes tea"})
    client.patch("/api/settings", json={"agent_max_steps": 5})

    response = client.get("/api/export")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/zip"
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = set(archive.namelist())
    assert {"conversations.json", "memory.json", "settings.json", "manifest.json"} <= names

    conversations = json.loads(archive.read("conversations.json"))
    assert conversations[0]["title"] == "Exported"
    assert conversations[0]["messages"][0]["content"] == "hello"
    assert "likes tea" in archive.read("memory.json").decode()
    assert "api_key" not in archive.read("connections.json").decode().lower()


def test_deletion_requires_the_exact_phrase(client):
    cid = client.post("/api/conversations", json={"title": "Keep me"}).json()["id"]
    response = client.post("/api/data/delete", json={"confirm": "delete"})
    assert response.status_code == 400
    assert client.get("/api/conversations").json()["conversations"]

    done = client.post("/api/data/delete", json={"confirm": "DELETE ALL DATA"})
    assert done.status_code == 200
    assert done.json()["status"] == "ok"
    assert client.get("/api/conversations").json()["conversations"] == []
    assert cid not in [c["id"] for c in client.get("/api/conversations").json()["conversations"]]


# --------------------------------------------------------------- assistants
def test_assistant_crud_and_instruction_injection(client):
    created = client.post("/api/assistants", json={
        "name": "Editor", "instructions": "Always answer in one sentence.",
        "greeting": "Ready.", "icon": "✎"}).json()["assistant"]
    assert created["name"] == "Editor"

    listed = client.get("/api/assistants").json()["assistants"]
    assert [a["id"] for a in listed] == [created["id"]]

    patched = client.patch(f"/api/assistants/{created['id']}",
                           json={"model": "some-model"}).json()["assistant"]
    assert patched["model"] == "some-model"

    cid = client.post("/api/conversations", json={"title": "Chat"}).json()["id"]
    messages = _messages_for(client.state, ChatRequest(message="hi", assistant_id=created["id"]),
                             cid)
    assert "Always answer in one sentence." in messages[0]["content"]
    assert "Editor" in messages[0]["content"]

    assert client.delete(f"/api/assistants/{created['id']}").json()["status"] == "ok"
    assert client.patch("/api/assistants/nope", json={"name": "x"}).status_code == 404


def test_templates_can_be_created_and_deleted(client):
    template = client.post("/api/templates", json={
        "name": "Summarise", "body": "Summarise the following in 5 bullets:",
        "category": "writing"}).json()["template"]
    assert client.get("/api/assistants").json()["templates"][0]["name"] == "Summarise"
    assert client.delete(f"/api/templates/{template['id']}").json()["status"] == "ok"
    assert client.get("/api/assistants").json()["templates"] == []


# ------------------------------------------------------------------- sharing
def test_sharing_is_off_until_enabled_and_then_works(client):
    cid = client.post("/api/conversations", json={"title": "Public chat"}).json()["id"]
    client.post("/api/chat/sync", json={"message": "what is 2+2", "conversation_id": cid})

    blocked = client.post(f"/api/conversations/{cid}/share", json={})
    assert blocked.status_code == 403

    client.patch("/api/settings", json={"share_links_enabled": True})
    share = client.post(f"/api/conversations/{cid}/share", json={}).json()["share"]
    assert share["active"] is True

    page = client.get(share["url"])
    assert page.status_code == 200
    assert "Public chat" in page.text
    assert "what is 2+2" in page.text
    assert "noindex" in page.text

    assert client.delete(f"/api/shares/{share['token']}").json()["status"] == "revoked"
    assert client.get(share["url"]).status_code == 410


def test_unknown_share_is_404(client):
    assert client.get("/share/does-not-exist").status_code == 404


def test_expired_share_is_gone(client):
    client.patch("/api/settings", json={"share_links_enabled": True})
    cid = client.post("/api/conversations", json={"title": "Short"}).json()["id"]
    share = client.post(f"/api/conversations/{cid}/share", json={"ttl_hours": 1}).json()["share"]
    client.state.shares._conn.execute("UPDATE shares SET expires_at=? WHERE token=?",
                                      (1.0, share["token"]))
    client.state.shares._conn.commit()
    assert client.get(share["url"]).status_code == 410


# ------------------------------------------------------- editing and branching
def test_editing_a_message_drops_what_followed(client):
    cid = client.post("/api/conversations", json={"title": "Edit"}).json()["id"]
    client.post("/api/chat/sync", json={"message": "first question", "conversation_id": cid})
    messages = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assert len(messages) == 2

    edited = client.patch(f"/api/conversations/{cid}/messages/{messages[0]['id']}",
                          json={"content": "better question"}).json()
    assert edited["removed"] == 2
    assert [m["content"] for m in edited["messages"]] == ["better question"]
    assert edited["messages"][0]["role"] == "user"


def test_branching_copies_history_into_a_new_conversation(client):
    cid = client.post("/api/conversations", json={"title": "Original"}).json()["id"]
    client.post("/api/chat/sync", json={"message": "one", "conversation_id": cid})
    client.post("/api/chat/sync", json={"message": "two", "conversation_id": cid})
    messages = client.get(f"/api/conversations/{cid}/messages").json()["messages"]

    branch = client.post(f"/api/conversations/{cid}/branch",
                         json={"message_id": messages[1]["id"]}).json()["conversation"]
    assert branch["id"] != cid
    assert branch["title"].endswith("(branch)")
    copied = client.get(f"/api/conversations/{branch['id']}/messages").json()["messages"]
    assert [m["content"] for m in copied] == [m["content"] for m in messages[:2]]

    assert client.post(f"/api/conversations/{cid}/branch",
                       json={"message_id": "missing"}).status_code == 404


def test_regenerate_replaces_the_last_answer(client):
    cid = client.post("/api/conversations", json={"title": "Regen"}).json()["id"]
    client.post("/api/chat/sync", json={"message": "question", "conversation_id": cid})
    client.state.router = FakeStreamRouter("A better answer.")

    with client.stream("POST", f"/api/conversations/{cid}/regenerate") as response:
        events = [json.loads(line[6:]) for line in response.iter_lines()
                  if line.startswith("data: ")]
    assert events[0]["data"]["regenerated"] is True
    assert any(e["type"] == "token" for e in events)

    messages = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assistants = [m for m in messages if m["role"] == "assistant"]
    assert len(assistants) == 1
    assert assistants[0]["content"] == "A better answer."
    assert len([m for m in messages if m["role"] == "user"]) == 1


def test_regenerate_without_a_question_is_400(client):
    cid = client.post("/api/conversations", json={"title": "Empty"}).json()["id"]
    assert client.post(f"/api/conversations/{cid}/regenerate").status_code == 400
