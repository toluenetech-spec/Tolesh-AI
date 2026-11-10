"""Phase 7 — connector routes, including the OAuth redirect and approvals."""

from __future__ import annotations

import json
import threading
from dataclasses import replace
from http.server import ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from tolesh.connectors.providers import PROVIDERS
from tolesh.server import create_app
from tolesh.server.app import AppState

from .test_tolesh_connectors import _Handler, TOKENS_ISSUED

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    monkeypatch.setitem(PROVIDERS, "github",
                        replace(PROVIDERS["github"], token_url=f"{base}/token"))
    TOKENS_ISSUED.clear()
    TOKENS_ISSUED.append("ghp_valid_token")
    yield base
    server.shutdown()
    server.server_close()


@pytest.fixture
def client(tmp_path, monkeypatch, fake_provider):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_CONNECTOR_KEY_PATH", str(tmp_path / "connector.key"))
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.fake_provider_base = fake_provider  # type: ignore[attr-defined]
        yield test_client
    state.close()


def _connect(client: TestClient, base: str) -> dict:
    response = client.post("/api/connectors/token",
                           json={"provider": "github", "token": "ghp_valid_token",
                                 "base_url": base, "label": "work"})
    assert response.status_code == 200, response.text
    return response.json()["connection"]


def test_catalogue_is_listed_even_with_no_connections(client):
    payload = client.get("/api/connectors").json()
    assert payload["connections"] == []
    assert {p["id"] for p in payload["providers"]} >= {"github", "gmail", "slack"}


def test_token_connection_never_echoes_the_secret(client, fake_provider):
    connection = _connect(client, fake_provider)
    assert connection["account"] == "octocat"
    body = client.get("/api/connectors").text
    assert "ghp_valid_token" not in body


def test_oauth_start_returns_an_authorize_url_and_callback_completes(client):
    start = client.post("/api/connectors/oauth/start", json={
        "provider": "github", "client_id": "cid", "client_secret": "sec",
        "redirect_uri": "http://127.0.0.1:8787/api/connectors/oauth/callback",
        "scopes": ["repo"], "base_url": client.fake_provider_base,
    }).json()
    assert start["authorize_url"].startswith("https://github.com/login/oauth/authorize")
    assert "code_challenge=" in start["authorize_url"]

    page = client.get("/api/connectors/oauth/callback",
                      params={"code": "abc", "state": start["state"]})
    assert page.status_code == 200
    assert "Connected" in page.text

    connections = client.get("/api/connectors").json()["connections"]
    assert len(connections) == 1
    assert connections[0]["provider"] == "github"


def test_callback_with_a_bad_state_is_refused(client):
    page = client.get("/api/connectors/oauth/callback",
                      params={"code": "abc", "state": "forged"})
    assert page.status_code == 400
    assert "not connected" in page.text.lower()


def test_callback_survives_the_api_token_guard(tmp_path, monkeypatch, fake_provider):
    """A provider redirect cannot send a bearer header; state is the credential."""
    monkeypatch.setenv("TOLESH_CONNECTOR_KEY_PATH", str(tmp_path / "k.key"))
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False,
                     api_token="secret-token")
    with TestClient(app) as client:
        assert client.get("/api/connectors").status_code == 401
        page = client.get("/api/connectors/oauth/callback",
                          params={"code": "x", "state": "y"})
        assert page.status_code == 400  # reached the handler, not blocked by auth
    state.close()


def test_write_action_is_parked_then_runs_after_approval(client, fake_provider):
    connection = _connect(client, fake_provider)
    pending = client.post(
        f"/api/connectors/{connection['id']}/actions/create_issue",
        json={"args": {"owner": "octocat", "repo": "hello", "title": "From the API"}},
    ).json()
    assert pending["status"] == "pending-approval"
    action = pending["approval"]

    client.post(f"/api/permissions/{action['id']}/approve",
                json={"token": action["token"], "execute": False})

    done = client.post(
        f"/api/connectors/{connection['id']}/actions/create_issue",
        json={"args": {"owner": "octocat", "repo": "hello", "title": "From the API"},
              "approval_id": action["id"], "approval_token": action["token"]},
    ).json()
    assert done["status"] == "ok"
    assert done["result"]["number"] == 1


def test_read_action_runs_immediately(client, fake_provider):
    connection = _connect(client, fake_provider)
    result = client.post(
        f"/api/connectors/{connection['id']}/actions/list_repositories",
        json={"args": {"limit": 5}}).json()
    assert result["status"] == "ok"
    assert result["result"]["repositories"][0]["name"] == "octocat/hello"


def test_unknown_connection_and_action_are_404(client, fake_provider):
    connection = _connect(client, fake_provider)
    assert client.post("/api/connectors/nope/test").status_code == 400
    assert client.post(f"/api/connectors/{connection['id']}/actions/not_real",
                       json={"args": {}}).status_code == 404
    assert client.delete("/api/connectors/nope").status_code == 404


def test_revoking_a_connection_removes_it_from_the_list(client, fake_provider):
    connection = _connect(client, fake_provider)
    assert client.delete(f"/api/connectors/{connection['id']}").json()["status"] == "ok"
    assert client.get("/api/connectors").json()["connections"] == []
