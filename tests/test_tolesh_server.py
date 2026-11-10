"""Integration tests for the shared Tolesh HTTP/SSE API contract.

The same ``/api`` surface is consumed by the desktop QtWebEngine shell and is
intended for future web and Android clients. These tests exercise it in-process
with an in-memory database and the deterministic offline engine — no network,
no native audio, no provider credentials and no writes outside ``tmp_path``.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from tolesh.ai import ModelRouter, Registry
from tolesh.server import create_app
from tolesh.server.app import AppState

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A fully offline API client whose router can only reach the local engine."""
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.delenv("TOLESH_DEFAULT_MODEL", raising=False)

    state = AppState(in_memory=True)
    offline = Registry.from_defaults().provider("offline")
    assert offline is not None
    state.registry = Registry({"offline": offline})
    state.router = ModelRouter(state.registry, state.ledger, offline_delay=0)

    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        yield test_client
    # lifespan deliberately does not close a caller-supplied state
    state.close()


def test_health_and_config_are_key_free(client):
    health = client.get("/api/health").json()
    assert health["status"] == "ok"
    assert health["product"] == "Tolesh"
    assert health["providers_configured"] == ["offline"]

    config = client.get("/api/config").json()
    assert config["default_model"] == ""
    assert "sk-" not in json.dumps(config)
    assert all(p["api_key"] != "sk-" for p in config["providers"])


def test_config_never_leaks_a_configured_key(monkeypatch, tmp_path):
    monkeypatch.setenv("TOLESH_JEV_API_KEY", "sk-top-secret-123")
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    try:
        with TestClient(app) as c:
            body = json.dumps(c.get("/api/config").json())
            assert "sk-top-secret-123" not in body
            jev = next(p for p in c.get("/api/providers").json()["providers"]
                       if p["name"] == "jev")
            assert jev["configured"] is True
            assert jev["api_key"] == "***"
    finally:
        state.close()


def test_models_and_providers_endpoints(client):
    models = client.get("/api/models").json()["models"]
    assert len(models) == 1
    assert models[0]["id"] == "offline:tolesh-offline"
    assert client.get("/api/models", params={"task": "code"}).status_code == 200
    providers = client.get("/api/providers").json()["providers"]
    assert [p["name"] for p in providers] == ["offline"]


def test_chat_sync_persists_both_turns(client):
    response = client.post("/api/chat/sync", json={"message": "How far?"})
    assert response.status_code == 200
    body = response.json()
    assert body["text"].strip()
    assert body["model"] == "offline:tolesh-offline"
    assert body["usage"]["total_tokens"] > 0

    cid = body["conversation_id"]
    turns = client.get(f"/api/conversations/{cid}/messages").json()["messages"]
    assert [t["role"] for t in turns] == ["user", "assistant"]
    assert turns[0]["content"] == "How far?"
    assert client.get("/api/usage").json()["requests"] >= 1


def test_chat_sse_stream_contract(client):
    response = client.post("/api/chat", json={"message": "Wetin dey?"})
    assert response.status_code == 200
    assert "text/event-stream" in response.headers["content-type"]
    frames = [json.loads(line[6:]) for line in response.text.splitlines()
              if line.startswith("data: ")]
    types = [frame["type"] for frame in frames]

    assert frames[0]["type"] == "meta"
    assert frames[0]["data"]["conversation_id"]
    assert "token" in types and "usage" in types and "done" in types
    assert "error" not in types
    assert all("type" in frame and "data" in frame for frame in frames)


def test_conversation_crud_endpoints(client):
    created = client.post("/api/conversations", json={"title": "Evening"}).json()
    cid = created["id"]
    assert created["title"] == "Evening"
    assert client.patch(f"/api/conversations/{cid}",
                        json={"title": "Renamed"}).json()["status"] == "ok"
    assert any(c["title"] == "Renamed" for c in
               client.get("/api/conversations").json()["conversations"])
    assert client.patch("/api/conversations/missing",
                        json={"title": "x"}).status_code == 404
    assert client.delete(f"/api/conversations/{cid}").json()["status"] == "ok"
    assert client.delete(f"/api/conversations/{cid}").status_code == 404


def test_memory_endpoints_and_forget(client):
    created = client.post("/api/memory", json={
        "category": "preference", "content": "Prefers short answers",
    }).json()
    assert created["category"] == "preference"
    assert client.get("/api/memory").json()["stats"]["total"] == 1
    assert client.get("/api/memory", params={"q": "short"}).json()["items"]

    assert client.post("/api/memory", json={
        "category": "invalid", "content": "x",
    }).status_code == 422  # pydantic Literal rejects unknown categories
    assert client.post("/api/memory/forget",
                       json={"confirm": "maybe"}).status_code == 400
    assert client.post("/api/memory/forget",
                       json={"confirm": "FORGET EVERYTHING"}).json()["deleted"] == 1
    assert client.get("/api/memory").json()["stats"]["total"] == 0

    another = client.post("/api/memory", json={
        "category": "task", "content": "Ship the desktop build",
    }).json()
    assert client.delete(f"/api/memory/{another['id']}").status_code == 200
    assert client.delete(f"/api/memory/{another['id']}").status_code == 404


def test_permission_flow_deny_then_approve(client):
    pending = client.post("/api/tools/invoke", json={
        "tool": "write_text_file", "args": {"path": "note.txt"},
        "reason": "test",
    }).json()
    assert pending["status"] == "pending-confirmation"
    assert pending["token"]
    aid = pending["id"]
    assert client.get("/api/permissions/pending").json()["pending"][0]["id"] == aid

    assert client.post(f"/api/permissions/{aid}/deny", json={
        "token": "wrong", "execute": False,
    }).status_code == 400

    denied = client.post(f"/api/permissions/{aid}/deny", json={
        "token": pending["token"], "execute": False,
    }).json()
    assert denied["decision"]["status"] == "denied"
    assert client.get("/api/permissions/pending").json()["pending"] == []

    # Approve without executing: no application is actually launched.
    second = client.post("/api/tools/invoke", json={
        "tool": "open_application", "args": {"name": "browser"},
    }).json()
    approved = client.post(f"/api/permissions/{second['id']}/approve", json={
        "token": second["token"], "execute": False,
    }).json()
    assert approved["decision"]["status"] == "approved"
    assert "execution" not in approved
    assert any(e["status"] in ("approved", "denied") for e in
               client.get("/api/permissions/audit").json()["entries"])


def test_tools_endpoints(client):
    tools = client.get("/api/tools").json()["tools"]
    by_name = {t["name"]: t for t in tools}
    assert by_name["get_time"]["risk"] == "safe"
    assert by_name["delete_file"]["implemented"] is False
    assert client.post("/api/tools/invoke",
                       json={"tool": "get_time"}).json()["status"] == "ok"
    assert client.post("/api/tools/invoke",
                       json={"tool": "run_command"}).json()["status"] == "pending-confirmation"


def test_api_token_guard(monkeypatch, tmp_path):
    monkeypatch.setenv("TOLESH_API_TOKEN", "local-secret")
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    try:
        with TestClient(app) as c:
            assert c.get("/api/health").status_code == 200  # health is exempt
            assert c.get("/api/config").status_code == 401
            assert c.get("/api/config", headers={
                "Authorization": "Bearer local-secret",
            }).status_code == 200
    finally:
        state.close()


def test_static_web_can_be_served_from_a_given_directory(tmp_path):
    (tmp_path / "index.html").write_text(
        "<!doctype html><title>Tolesh</title><h1>Tolesh</h1>", encoding="utf-8")
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=True)
    try:
        with TestClient(app) as c:
            response = c.get("/")
            assert response.status_code == 200
            assert "Tolesh" in response.text
    finally:
        state.close()
