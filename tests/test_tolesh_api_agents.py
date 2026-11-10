"""Phase 8 — agent routes, including a real MCP tool behind an approval."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tolesh.server import create_app
from tolesh.server.app import AppState

from .test_tolesh_agents import ScriptedRouter, action, final

pytestmark = pytest.mark.integration

FIXTURES = Path(__file__).parent / "fixtures"


def _events(response) -> list[dict]:
    out = []
    for line in response.iter_lines():
        if line.startswith("data: "):
            out.append(json.loads(line[6:]))
    return out


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv("TOLESH_API_TOKEN", raising=False)
    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    monkeypatch.setenv("TOLESH_CONNECTOR_KEY_PATH", str(tmp_path / "c.key"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


def test_agent_tools_lists_the_sandbox_and_files(client):
    payload = client.get("/api/agents/tools").json()
    names = {t["name"] for t in payload["tools"]}
    assert "run_python" in names
    assert "search_files" in names
    sandbox = next(t for t in payload["tools"] if t["name"] == "run_python")
    assert sandbox["risk"] == "elevated"


def test_run_streams_steps_and_finishes(client):
    client.state.router = ScriptedRouter([action("run_python", code="print(21*2)"),
                                          final("It is 42.")])
    run = client.post("/api/agents/runs", json={"goal": "What is 21*2?"}).json()["run"]
    with client.stream("POST", f"/api/agents/runs/{run['id']}/run") as response:
        events = _events(response)
    kinds = [e["type"] for e in events]
    assert kinds[0] == "run" and kinds[-1] == "final"
    assert "tool_result" in kinds
    assert events[-1]["data"]["answer"] == "It is 42."

    detail = client.get(f"/api/agents/runs/{run['id']}").json()
    assert detail["run"]["status"] == "done"
    assert detail["messages"]


def test_run_requires_a_goal(client):
    assert client.post("/api/agents/runs", json={"goal": "  "}).status_code == 400


def test_unknown_run_is_404(client):
    assert client.get("/api/agents/runs/nope").status_code == 404
    assert client.post("/api/agents/runs/nope/run").status_code == 404


def test_mcp_tool_approval_round_trip_through_the_agent(client):
    """The whole chain: MCP server -> tool policy -> agent -> approval -> resume."""
    created = client.post("/api/mcp/servers", json={
        "name": "fixture", "transport": "stdio", "command": sys.executable,
        "args": [str(FIXTURES / "mcp_fixture_server.py")], "timeout_sec": 30,
        "tool_permissions": {"echo": "ask"},
    })
    assert created.status_code == 200, created.text

    client.state.router = ScriptedRouter([
        action("mcp__fixture__echo", text="hello from the agent"),
        action("mcp__fixture__echo", text="hello from the agent"),
        final("The tool echoed the text."),
    ])
    run = client.post("/api/agents/runs", json={"goal": "Echo something"}).json()["run"]

    with client.stream("POST", f"/api/agents/runs/{run['id']}/run") as response:
        first = _events(response)
    assert first[-1]["type"] == "approval_required"
    approval = first[-1]["data"]["approval"]

    client.post(f"/api/permissions/{approval['id']}/approve",
                json={"token": approval["token"], "execute": False})

    with client.stream("POST", f"/api/agents/runs/{run['id']}/resume") as response:
        second = _events(response)
    assert second[-1]["type"] == "final", second
    tool_results = [e for e in second if e["type"] == "tool_result"]
    assert tool_results and "hello from the agent" in tool_results[0]["data"]["text"]
    assert client.get(f"/api/agents/runs/{run['id']}").json()["run"]["status"] == "done"


def test_cancel_marks_the_run(client):
    run = client.post("/api/agents/runs", json={"goal": "Long task"}).json()["run"]
    assert client.post(f"/api/agents/runs/{run['id']}/cancel").json()["status"] == "cancelling"
    assert client.get(f"/api/agents/runs/{run['id']}").json()["run"]["status"] == "cancelled"


def test_runs_can_be_listed_and_deleted(client):
    run = client.post("/api/agents/runs", json={"goal": "Something"}).json()["run"]
    listed = client.get("/api/agents/runs").json()["runs"]
    assert any(r["id"] == run["id"] for r in listed)
    assert client.delete(f"/api/agents/runs/{run['id']}").json()["status"] == "ok"
    assert client.get(f"/api/agents/runs/{run['id']}").status_code == 404
