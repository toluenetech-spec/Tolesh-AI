"""Phase 6 — MCP integration, tested against real MCP servers.

The stdio server is ``tests/fixtures/mcp_fixture_server.py`` (official SDK),
and the HTTP test starts ``mcp_http_fixture_server.py`` on a free port. These
are genuine protocol conversations, not stubs.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tolesh.mcp_bridge import (MCPManager, MCPServerStore, mask_secret,
                               split_tool_id, tool_id)

FIXTURES = Path(__file__).parent / "fixtures"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def store(tmp_path):
    return MCPServerStore(tmp_path / "mcp.json")


@pytest.fixture
def stdio_server(store):
    store.upsert("fixture", {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(FIXTURES / "mcp_fixture_server.py")],
        "timeout_sec": 30,
    })
    return "fixture"


@pytest.fixture
def manager(store):
    mgr = MCPManager(store)
    yield mgr
    mgr.close()


# ------------------------------------------------------------------- store
def test_store_validates_names_transports_and_commands(store):
    with pytest.raises(ValueError):
        store.upsert("bad name!", {"transport": "stdio", "command": "x"})
    with pytest.raises(ValueError):
        store.upsert("s", {"transport": "websocket"})
    with pytest.raises(ValueError):
        store.upsert("s", {"transport": "stdio"})
    with pytest.raises(ValueError):
        store.upsert("s", {"transport": "http", "url": "file:///etc/passwd"})
    with pytest.raises(ValueError):
        store.upsert("s", {"transport": "stdio", "command": "x", "default_policy": "maybe"})


def test_secrets_are_masked_and_a_masked_value_keeps_the_real_one(store):
    store.upsert("gh", {"transport": "http", "url": "https://example.test/mcp",
                        "headers": {"Authorization": "Bearer supersecretvalue"}})
    public = store.get("gh")
    assert "supersecretvalue" not in str(public)
    assert public["headers"]["Authorization"].startswith("Bea")

    # Saving the form again with the masked preview must not overwrite the key.
    store.upsert("gh", {"headers": {"Authorization": public["headers"]["Authorization"]}})
    assert store.raw("gh")["headers"]["Authorization"] == "Bearer supersecretvalue"


def test_per_tool_policy_falls_back_to_the_server_default(store):
    cfg = {"default_policy": "ask", "tool_permissions": {"echo": "allow"}}
    assert MCPServerStore.policy_for(cfg, "echo") == "allow"
    assert MCPServerStore.policy_for(cfg, "delete_everything") == "ask"


def test_mask_secret_never_returns_a_usable_value():
    assert mask_secret("") == ""
    assert mask_secret("short") == "***"
    assert "x" not in mask_secret("abcdefghijklmnop")[3:-2]


# ------------------------------------------------------- stdio end to end
def test_stdio_server_connects_lists_and_invokes(manager, stdio_server):
    result = manager.test(stdio_server)
    assert result["ok"] is True, result
    assert result["server_info"]["name"] == "tolesh-fixture"
    assert result["tools"] == 3
    assert result["resources"] == 1
    assert result["prompts"] == 1

    echo = manager.invoke(stdio_server, "echo", {"text": "hello"})
    assert echo["status"] == "ok"
    assert echo["text"] == "hello"

    added = manager.invoke(stdio_server, "add", {"a": 2, "b": 40})
    assert added["text"] == "42"


def test_tool_level_failure_is_reported_as_an_error_not_a_crash(manager, stdio_server):
    result = manager.invoke(stdio_server, "explode", {})
    assert result["status"] == "error"
    assert result["is_error"] is True
    assert "fixture failure on purpose" in result["text"]


def test_resources_and_prompts_are_readable(manager, stdio_server):
    resources = manager.resources(stdio_server)
    assert resources[0]["uri"] == "fixture://greeting"
    read = manager.read_resource(stdio_server, "fixture://greeting")
    assert read["text"] == "hello from the fixture server"

    prompt = manager.get_prompt(stdio_server, "summarise", {"topic": "MCP"})
    assert prompt["messages"][0]["text"] == "Summarise MCP in three sentences."


def test_unknown_tool_is_refused_with_the_available_names(manager, stdio_server):
    result = manager.invoke(stdio_server, "not_a_tool", {})
    assert result["status"] == "error"
    assert "echo" in result["available"]


def test_session_is_reused_across_calls(manager, stdio_server):
    manager.connect(stdio_server)
    first = manager._sessions[stdio_server].opened_at
    manager.invoke(stdio_server, "echo", {"text": "again"})
    manager.invoke(stdio_server, "echo", {"text": "and again"})
    assert manager._sessions[stdio_server].opened_at == first


def test_bad_command_reports_a_useful_error(store):
    store.upsert("broken", {"transport": "stdio", "command": "/nonexistent/mcp-binary"})
    mgr = MCPManager(store)
    try:
        result = mgr.test("broken")
        assert result["ok"] is False
        assert "not found" in result["error"].lower() or "connect" in result["error"].lower()
        assert mgr.status("broken")["state"] == "error"
    finally:
        mgr.close()


def test_disabled_server_is_not_reachable(store, stdio_server):
    store.set_enabled(stdio_server, False)
    mgr = MCPManager(store)
    try:
        result = mgr.test(stdio_server)
        assert result["ok"] is False
        assert "switched off" in result["error"]
    finally:
        mgr.close()


# ------------------------------------------------------------ permissions
def test_deny_policy_blocks_without_running(store, stdio_server):
    store.upsert(stdio_server, {"tool_permissions": {"echo": "deny"}})
    mgr = MCPManager(store)
    try:
        result = mgr.invoke(stdio_server, "echo", {"text": "should not run"})
        assert result["status"] == "denied"
    finally:
        mgr.close()


def test_ask_policy_parks_the_call_until_it_is_approved(store, stdio_server, tmp_path):
    from tolesh.permissions import PermissionGate

    store.upsert(stdio_server, {"tool_permissions": {"echo": "ask"}})
    gate = PermissionGate(db_path=str(tmp_path / "gate.db"))
    mgr = MCPManager(store, gate=gate)
    try:
        pending = mgr.invoke(stdio_server, "echo", {"text": "needs approval"})
        assert pending["status"] == "pending-approval"
        assert pending["approval"]["status"] == "pending"

        approved = gate.approve(pending["approval"]["id"], pending["approval"]["token"])
        assert approved["status"] == "approved"

        ran = mgr.invoke(stdio_server, "echo", {"text": "needs approval"},
                         approval=approved)
        assert ran["status"] == "ok"
        assert ran["text"] == "needs approval"
    finally:
        mgr.close()
        gate.close()


def test_call_log_records_arguments_without_leaking_tokens(manager, stdio_server):
    manager.invoke(stdio_server, "echo", {"text": "logged", "api_key": "sk-live-123456"})
    calls = manager.calls()
    assert calls[0]["tool"] == "echo"
    assert "sk-live-123456" not in str(calls)


def test_qualified_tool_ids_round_trip():
    assert tool_id("github", "search") == "mcp__github__search"
    assert split_tool_id("mcp__github__search") == ("github", "search")
    assert split_tool_id("mcp__noseparator") is None
    assert split_tool_id("builtin") is None


# ------------------------------------------------------------- HTTP transport
@pytest.fixture
def http_server(tmp_path):
    port = _free_port()
    env = {**os.environ, "FIXTURE_PORT": str(port), "PYTHONPATH": "src"}
    proc = subprocess.Popen(
        [sys.executable, str(FIXTURES / "mcp_http_fixture_server.py")],
        env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).resolve().parents[1]),
    )
    deadline = time.time() + 25
    while time.time() < deadline:
        with socket.socket() as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                break
        time.sleep(0.2)
    else:
        proc.terminate()
        pytest.fail("the HTTP fixture server did not start")
    yield port
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover
        proc.kill()


def test_http_transport_connects_and_invokes(store, http_server):
    store.upsert("web", {
        "transport": "http",
        "url": f"http://127.0.0.1:{http_server}/mcp",
        "headers": {"Authorization": "Bearer fixture-token"},
        "timeout_sec": 20,
    })
    mgr = MCPManager(store)
    try:
        result = mgr.test("web")
        assert result["ok"] is True, result
        assert result["tools"] == 2
        echo = mgr.invoke("web", "echo", {"text": "over http"})
        assert echo["status"] == "ok"
        assert echo["text"] == "over http"
    finally:
        mgr.close()


def test_http_transport_reports_an_unreachable_server(store):
    store.upsert("dead", {"transport": "http", "url": f"http://127.0.0.1:{_free_port()}/mcp",
                          "timeout_sec": 3})
    mgr = MCPManager(store)
    try:
        result = mgr.test("dead")
        assert result["ok"] is False
        assert result["error"]
    finally:
        mgr.close()
