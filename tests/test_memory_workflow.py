"""The end-to-end memory workflow the brief specifies, in one test.

1. a conversation that contains long-term information
2. Tolesh saves it without being asked
3. restart the application
4. start a completely new conversation
5. ask something that needs the earlier information
6. the right memory is retrieved *and reaches the model*
7. correct that memory
8. the old value stops being treated as current
9. ask Tolesh to forget something
10. the forgotten fact is not retrieved again

"Reaches the model" is checked by recording the messages the router actually
receives — not by inspecting the store. A memory that is stored and never put in
front of the model is not memory, it is a log file.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tolesh.ai import ModelRouter, Registry
from tolesh.server import create_app
from tolesh.server.app import AppState

pytestmark = pytest.mark.integration


class RecordingRouter:
    """Wraps the offline router and keeps every prompt it was given."""

    def __init__(self, inner):
        self._inner = inner
        self.prompts: list[list[dict[str, str]]] = []

    def select(self, task: str = "chat", preferred: str | None = None):
        return self._inner.select(task, preferred)

    def stream(self, messages, **kwargs):
        self.prompts.append([dict(m) for m in messages])
        yield from self._inner.stream(messages, **kwargs)

    def complete(self, messages, **kwargs):
        self.prompts.append([dict(m) for m in messages])
        return self._inner.complete(messages, **kwargs)

    def probe(self, messages, **kwargs):
        return self._inner.probe(messages, **kwargs)

    @property
    def last_system(self) -> str:
        assert self.prompts, "no request reached the router"
        return self.prompts[-1][0]["content"]


def _open(tmp_path, name: str):
    state = AppState(db_path=str(tmp_path / name), memory_debounce=0.01)
    offline = Registry.from_defaults().provider("offline")
    state.registry = Registry({"offline": offline})
    router = RecordingRouter(ModelRouter(state.registry, state.ledger, offline_delay=0))
    state.router = router
    state.memory_service._router = router          # model-mode extraction, if used
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    return state, router, TestClient(app)


def _wait_for_memory(state: AppState, timeout: float = 5.0) -> list[dict]:
    deadline = time.time() + timeout
    while time.time() < deadline:
        items = state.memory.list()
        if items:
            return items
        time.sleep(0.02)
    return state.memory.list()


def test_the_full_memory_workflow(tmp_path):
    db = "workflow.db"

    # 1. A conversation carrying durable facts, and nothing else worth keeping.
    state, router, client = _open(tmp_path, db)
    cid = client.post("/api/conversations", json={"title": "About me"}).json()["id"]
    client.post("/api/chat/sync", json={
        "message": "My name is Ada and I prefer Python over Node. "
                   "I live in Lagos. Thanks!",
        "conversation_id": cid})

    # 2. Learned automatically — no /api/memory call anywhere above.
    items = _wait_for_memory(state)
    assert items, "the background worker stored nothing"
    learned = {item["category"] for item in items}
    assert "personal" in learned
    assert all(item["origin"] in ("auto", "correction") for item in items)

    # 3. Restart: a new AppState over the same database, as a relaunch would.
    state.close()
    state, router, client = _open(tmp_path, db)

    # 4. A completely new conversation.
    cid2 = client.post("/api/conversations", json={"title": "Fresh"}).json()["id"]

    # 5/6. The question needs the earlier information, and it reaches the model.
    client.post("/api/chat/sync", json={"message": "What is my name?",
                                        "conversation_id": cid2})
    system = router.last_system
    assert "Ada" in system, system[-800:]
    assert "transparent memory" in system

    # 7. Correct it. The correction is phrased the way a person would.
    client.post("/api/chat/sync", json={"message": "Actually my name is Adaeze now.",
                                        "conversation_id": cid2})
    _wait_for_memory(state)
    deadline = time.time() + 5
    while time.time() < deadline:
        if any("Adaeze" in item["content"] for item in state.memory.list()):
            break
        time.sleep(0.02)

    # 8. The old value is no longer current, and no longer reaches the model.
    active = [item["content"] for item in state.memory.list()]
    assert any("Adaeze" in text for text in active)
    assert not any(text.rstrip(".") == "My name is Ada" for text in active)
    superseded = [item for item in state.memory.list(include_superseded=True)
                  if item["status"] == "superseded"]
    assert superseded, "the replaced fact should stay visible for audit"

    client.post("/api/chat/sync", json={"message": "What is my name?",
                                        "conversation_id": cid2})
    assert "Adaeze" in router.last_system
    # The replaced wording must be gone. The full stop matters: "Adaeze" itself
    # contains "Ada", so a bare substring check would always fail.
    assert "My name is Ada." not in router.last_system

    # 9. Forget the location.
    client.post("/api/chat/sync", json={"message": "Forget that I live in Lagos.",
                                        "conversation_id": cid2})
    deadline = time.time() + 5
    while time.time() < deadline:
        if not any("Lagos" in item["content"] for item in state.memory.list()):
            break
        time.sleep(0.02)
    assert not any("Lagos" in item["content"] for item in state.memory.list())
    assert state.memory.suppressions()

    # 10. It is not retrieved, and it cannot be relearned.
    client.post("/api/chat/sync", json={"message": "Where do I live?",
                                        "conversation_id": cid2})
    assert "Lagos" not in router.last_system
    client.post("/api/chat/sync", json={"message": "I live in Lagos by the way.",
                                        "conversation_id": cid2})
    time.sleep(0.3)
    assert not any("Lagos" in item["content"] for item in state.memory.list())

    state.close()


def test_a_conversation_with_nothing_durable_stores_nothing(tmp_path):
    state, _router, client = _open(tmp_path, "plain.db")
    cid = client.post("/api/conversations", json={"title": "Chat"}).json()["id"]
    client.post("/api/chat/sync", json={
        "message": "Explain list comprehensions in one line.",
        "conversation_id": cid})
    time.sleep(0.4)
    assert state.memory.list() == []
    state.close()


def test_memory_survives_a_real_process_restart(tmp_path):
    """Same database file, brand-new stores: the facts are still there.

    The AppState restart above already proves the connection closes and reopens,
    but a fresh process is the case that actually broke before (an in-memory
    default silently discarded everything).
    """
    import subprocess
    import sys
    import textwrap

    path = str(tmp_path / "restart.db")
    script = textwrap.dedent(f"""
        import sys
        from tolesh.server.app import AppState
        state = AppState(db_path={path!r}, memory_debounce=0.01)
        svc = state.memory_service
        if sys.argv[1] == "write":
            svc.apply_candidates(
                svc.extract_turns([{{"role": "user",
                                     "content": "My name is Ada and I prefer Python."}}]))
            print(len(state.memory.list()))
        else:
            print(len(state.memory.list()))
            print(bool(svc.retrieve("what is my name?")))
        state.close()
    """)
    env = {"PYTHONPATH": "src", "PATH": "/usr/local/bin:/usr/bin:/bin"}
    first = subprocess.run([sys.executable, "-c", script, "write"], capture_output=True,
                           text=True, env=env, cwd=".")
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "2"   # name + preference, split at "and"
    second = subprocess.run([sys.executable, "-c", script, "read"], capture_output=True,
                            text=True, env=env, cwd=".")
    assert second.returncode == 0, second.stderr
    assert second.stdout.split() == ["2", "True"]
