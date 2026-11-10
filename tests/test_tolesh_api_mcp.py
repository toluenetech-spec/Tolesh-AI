"""Phase 6 — the MCP HTTP surface.

Covers registration, validation, connectivity testing, invocation and the
approval round-trip through the same routes the web client calls.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tolesh.server import create_app
from tolesh.server.app import AppState

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        yield test_client
    state.close()


def _add_fixture(client: TestClient, **extra):
    body = {
        "name": "fixture",
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FIXTURES / "mcp_fixture_server.py")],
        "timeout_sec": 30,
        **extra,
    }
    response = client.post("/api/mcp/servers", json=body)
    assert response.status_code == 200, response.text
    return response.json()["server"]


def test_capabilities_reports_mcp_as_available(client):
    payload = client.get("/api/capabilities").json()
    assert payload["phases"]["mcp"]["available"] is True
    assert isinstance(payload["mcp_servers"], list)


def test_server_crud_and_masking(client):
    server = _add_fixture(client, env={"DEMO_TOKEN": "super-secret-token"})
    assert server["env"]["DEMO_TOKEN"] != "super-secret-token"

    listed = client.get("/api/mcp/servers").json()["servers"]
    assert [s["name"] for s in listed] == ["fixture"]
    assert "super-secret-token" not in str(listed)

    patched = client.patch("/api/mcp/servers/fixture",
                           json={"default_policy": "ask"}).json()["server"]
    assert patched["default_policy"] == "ask"

    assert client.delete("/api/mcp/servers/fixture").json()["status"] == "ok"
    assert client.get("/api/mcp/servers").json()["servers"] == []


def test_invalid_registration_is_a_400(client):
    response = client.post("/api/mcp/servers",
                           json={"name": "bad name", "transport": "stdio", "command": "x"})
    assert response.status_code == 400
    response = client.post("/api/mcp/servers",
                           json={"name": "ok", "transport": "http", "url": "not-a-url"})
    assert response.status_code == 400


def test_test_endpoint_connects_and_reports_counts(client):
    _add_fixture(client)
    payload = client.post("/api/mcp/servers/fixture/test").json()
    assert payload["ok"] is True, payload
    assert payload["tools"] == 3
    assert payload["resources"] == 1
    assert payload["prompts"] == 1


def test_tools_and_invocation_over_http(client):
    _add_fixture(client)
    tools = client.get("/api/mcp/tools").json()["tools"]
    assert {t["name"] for t in tools} >= {"echo", "add"}
    assert all(t["qualified"].startswith("mcp__fixture__") for t in tools)

    result = client.post("/api/mcp/invoke",
                         json={"server": "fixture", "tool": "add", "args": {"a": 1, "b": 41}}).json()
    assert result["status"] == "ok"
    assert result["text"] == "42"

    calls = client.get("/api/mcp/calls").json()["calls"]
    assert calls[0]["tool"] == "add"


def test_ask_policy_round_trip_through_the_approval_routes(client):
    _add_fixture(client, tool_permissions={"echo": "ask"})
    pending = client.post("/api/mcp/invoke",
                          json={"server": "fixture", "tool": "echo",
                                "args": {"text": "approved path"}}).json()
    assert pending["status"] == "pending-approval"
    action = pending["approval"]
    assert action["status"] == "pending"

    approved = client.post(f"/api/permissions/{action['id']}/approve",
                           json={"token": action["token"], "execute": False}).json()
    assert approved["decision"]["status"] == "approved"

    ran = client.post("/api/mcp/invoke",
                      json={"server": "fixture", "tool": "echo",
                            "args": {"text": "approved path"},
                            "approval_id": action["id"],
                            "approval_token": action["token"]}).json()
    assert ran["status"] == "ok"
    assert ran["text"] == "approved path"


def test_deny_policy_never_executes(client):
    _add_fixture(client, tool_permissions={"echo": "deny"})
    result = client.post("/api/mcp/invoke",
                         json={"server": "fixture", "tool": "echo",
                               "args": {"text": "blocked"}}).json()
    assert result["status"] == "denied"


def test_resources_and_prompts_over_http(client):
    _add_fixture(client)
    resources = client.get("/api/mcp/servers/fixture/resources").json()["resources"]
    assert resources[0]["uri"] == "fixture://greeting"
    read = client.post("/api/mcp/servers/fixture/resources/read",
                       json={"uri": "fixture://greeting"}).json()
    assert "hello from the fixture server" in read["text"]

    prompts = client.get("/api/mcp/servers/fixture/prompts").json()["prompts"]
    assert prompts[0]["name"] == "summarise"
    run = client.post("/api/mcp/servers/fixture/prompts/run",
                      json={"prompt": "summarise", "arguments": {"topic": "the API"}}).json()
    assert run["messages"][0]["text"] == "Summarise the API in three sentences."
