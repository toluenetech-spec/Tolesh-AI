"""Phase 7 — external connections.

Every request here goes over real HTTP to a local fake provider that speaks
the same shapes as GitHub (JSON API, form-encoded token endpoint). No real
account is used, and no test claims a live third-party connection.
"""

from __future__ import annotations

import base64
import json
import threading
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

from tolesh.connectors import (ConnectorError, ConnectorManager, ConnectorStore,
                               TokenVault, catalogue)
from tolesh.connectors import manager as manager_module
from tolesh.connectors.providers import PROVIDERS

ISSUES: list[dict] = []
TOKENS_ISSUED: list[str] = []


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silence the test output
        return

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorised(self) -> bool:
        header = self.headers.get("Authorization", "")
        token = header.replace("Bearer ", "")
        return token in TOKENS_ISSUED

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/user":
            if not self._authorised():
                return self._json(401, {"message": "Bad credentials"})
            return self._json(200, {"login": "octocat", "id": 1})
        if path == "/user/repos":
            if not self._authorised():
                return self._json(401, {"message": "Bad credentials"})
            return self._json(200, [{"full_name": "octocat/hello", "private": False,
                                     "updated_at": "2026-01-01T00:00:00Z",
                                     "description": "demo", "html_url": "https://x"}])
        if path == "/repos/octocat/hello/issues":
            return self._json(200, ISSUES)
        if path == "/repos/octocat/hello/contents/README.md":
            content = base64.b64encode(b"# hello").decode()
            return self._json(200, {"content": content, "encoding": "base64"})
        if path == "/auth.test":
            if not self._authorised():
                return self._json(401, {"ok": False, "error": "invalid_auth"})
            return self._json(200, {"ok": True, "team": "tolesh", "user": "bot"})
        return self._json(404, {"message": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode() if length else ""
        if parsed.path == "/token":
            form = {k: v[0] for k, v in parse_qs(raw).items()}
            if form.get("grant_type") == "refresh_token":
                token = "refreshed-token"
            else:
                token = "oauth-access-token"
            TOKENS_ISSUED.append(token)
            return self._json(200, {"access_token": token,
                                    "refresh_token": "refresh-token",
                                    "token_type": "bearer", "expires_in": 3600,
                                    "scope": "repo"})
        if parsed.path == "/repos/octocat/hello/issues":
            if not self._authorised():
                return self._json(401, {"message": "Bad credentials"})
            payload = json.loads(raw or "{}")
            issue = {"number": len(ISSUES) + 1, "title": payload.get("title"),
                     "state": "open", "html_url": "https://example.test/1"}
            ISSUES.append(issue)
            return self._json(201, issue)
        return self._json(404, {"message": "not found"})


@pytest.fixture
def fake_provider(monkeypatch):
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    # Point the GitHub provider's OAuth token endpoint at the fake, keeping
    # everything else (scopes, API paths, headers) exactly as shipped.
    monkeypatch.setitem(PROVIDERS, "github",
                        replace(PROVIDERS["github"], token_url=f"{base}/token"))
    ISSUES.clear()
    TOKENS_ISSUED.clear()
    TOKENS_ISSUED.append("ghp_valid_token")
    yield base
    server.shutdown()
    server.server_close()


@pytest.fixture
def mgr(tmp_path, fake_provider, monkeypatch):
    monkeypatch.setenv("TOLESH_CONNECTOR_KEY_PATH", str(tmp_path / "connector.key"))
    store = ConnectorStore(str(tmp_path / "connectors.db"), vault=TokenVault(tmp_path / "connector.key"))
    manager = ConnectorManager(store)
    yield manager
    manager.close()


def test_catalogue_exposes_the_six_requested_providers():
    ids = {p["id"] for p in catalogue()}
    assert {"github", "google_drive", "gmail", "google_calendar", "notion", "slack"} <= ids
    google = next(p for p in catalogue() if p["id"] == "gmail")
    assert google["supports_oauth"] and not google["supports_token"]


def test_token_connection_is_verified_then_stored_encrypted(mgr, tmp_path, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider,
                         label="work")
    assert conn["status"] == "connected"
    assert conn["account"] == "octocat"
    assert conn["has_token"] is True
    # The secret must not be readable from the public view or from the file.
    assert "ghp_valid_token" not in json.dumps(conn)
    raw = (tmp_path / "connectors.db").read_bytes()
    assert b"ghp_valid_token" not in raw


def test_a_rejected_token_is_not_kept(mgr, fake_provider):
    with pytest.raises(ConnectorError) as excinfo:
        mgr.add_token("github", "ghp_wrong", base_url=fake_provider)
    assert "401" in str(excinfo.value)
    assert mgr.connections() == []


def test_oauth_flow_uses_state_and_pkce_and_stores_the_token(mgr, fake_provider):
    start = mgr.begin_oauth("github", client_id="client-123",
                            client_secret="shh", redirect_uri="http://127.0.0.1:8787/cb",
                            scopes=["repo"])
    assert "state=" in start["authorize_url"]
    assert "code_challenge=" in start["authorize_url"]
    assert "S256" in start["authorize_url"]

    conn = mgr.complete_oauth(start["state"], "the-code", base_url=fake_provider)
    assert conn["provider"] == "github"
    assert conn["has_token"] and conn["has_refresh"]
    assert "oauth-access-token" not in json.dumps(conn)

    # A state is single use.
    with pytest.raises(ConnectorError) as excinfo:
        mgr.complete_oauth(start["state"], "the-code", base_url=fake_provider)
    assert excinfo.value.kind == "state"


def test_oauth_rejects_an_unknown_state(mgr):
    with pytest.raises(ConnectorError):
        mgr.complete_oauth("not-a-state", "code")


def test_oauth_requires_a_client_id(mgr):
    with pytest.raises(ConnectorError) as excinfo:
        mgr.begin_oauth("github", client_id="", redirect_uri="http://localhost/cb")
    assert "client id" in str(excinfo.value).lower()


def test_expired_token_is_refreshed_before_the_call(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    mgr.store.upsert(connection_id=conn["id"], provider="github",
                     access_token="expired-token", refresh_token="refresh-token",
                     expires_at=1.0, scopes=["repo"], label="work",
                     meta={"base_url": fake_provider, "client_id": "client-123"})
    assert mgr.store.get(conn["id"])["expired"] is True
    repos = mgr.run_action(conn["id"], "list_repositories", {"limit": 5})
    assert repos["repositories"][0]["name"] == "octocat/hello"
    assert mgr.store.get(conn["id"])["expired"] is False


def test_paths_cannot_escape_the_provider_host(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    for path in ("https://evil.test/steal", "//evil.test/steal", "/../etc/passwd"):
        with pytest.raises(ConnectorError):
            mgr.call(conn["id"], "GET", path)


def test_base_url_must_be_http_and_carry_no_credentials(mgr):
    with pytest.raises(ConnectorError):
        mgr.add_token("github", "ghp_valid_token", base_url="file:///etc/passwd")
    with pytest.raises(ConnectorError):
        mgr.add_token("github", "ghp_valid_token", base_url="http://user:pw@example.test")


def test_read_actions_run_and_write_actions_need_approval(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    actions = {a["name"]: a for a in mgr.actions(conn["id"])}
    assert actions["create_issue"]["requires_approval"] is True
    assert actions["list_issues"]["requires_approval"] is False

    listed = mgr.run_action(conn["id"], "list_issues", {"owner": "octocat", "repo": "hello"})
    assert listed["count"] == 0

    with pytest.raises(ConnectorError) as excinfo:
        mgr.run_action(conn["id"], "create_issue",
                       {"owner": "octocat", "repo": "hello", "title": "Bug"})
    assert excinfo.value.kind == "approval"

    created = mgr.run_action(conn["id"], "create_issue",
                             {"owner": "octocat", "repo": "hello", "title": "Bug"},
                             approved=True)
    assert created["number"] == 1
    assert mgr.run_action(conn["id"], "list_issues",
                          {"owner": "octocat", "repo": "hello"})["count"] == 1


def test_read_file_decodes_base64_content(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    result = mgr.run_action(conn["id"], "read_file",
                            {"owner": "octocat", "repo": "hello", "path": "README.md"})
    assert result["text"] == "# hello"


def test_unknown_action_is_reported(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    with pytest.raises(ConnectorError):
        mgr.run_action(conn["id"], "delete_everything", {})


def test_revoking_a_connection_removes_it(mgr, fake_provider):
    conn = mgr.add_token("github", "ghp_valid_token", base_url=fake_provider)
    assert mgr.delete(conn["id"]) is True
    assert mgr.connections() == []
    assert mgr.store.secrets(conn["id"]) is None


def test_every_declared_action_has_a_handler():
    """A provider must not advertise an action nothing can execute."""
    from tolesh.connectors.manager import _ACTIONS

    for prov in PROVIDERS.values():
        for action in prov.actions:
            assert (prov.id, action) in _ACTIONS, f"{prov.id}:{action} has no handler"
