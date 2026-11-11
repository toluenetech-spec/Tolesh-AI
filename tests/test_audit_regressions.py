"""Regression tests for two real failures found by the feature audit.

Both were found by driving the API, not by reading the code:

* exporting a data set that contained an uploaded file raised ``TypeError`` —
  ``ZipFile.write`` takes a path, and the code handed it a ``BytesIO``;
* two stores writing at once on a shared-cache in-memory database raised
  ``database table is locked``, because SQLite's busy handler does not retry
  table locks. It surfaced when the memory worker wrote while a request wrote
  settings.
"""

from __future__ import annotations

import io
import threading
import zipfile

import pytest
from fastapi.testclient import TestClient

from tolesh.ai import ModelRouter, Registry
from tolesh.memory_ext import LayeredMemory
from tolesh.server import create_app
from tolesh.server.app import AppState

pytestmark = pytest.mark.integration


@pytest.fixture
def client(tmp_path):
    state = AppState(in_memory=True, memory_debounce=0.01)
    # Offline-only: without this the router reaches for the providers configured
    # on this machine and each chat takes seconds.
    offline = Registry.from_defaults().provider("offline")
    state.registry = Registry({"offline": offline})
    state.router = ModelRouter(state.registry, state.ledger, offline_delay=0)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        yield test_client, state
    state.close()


def test_export_includes_uploaded_files(client):
    api, _state = client
    uploaded = api.post("/api/attachments", params={"filename": "notes.txt"},
                        content=b"hello from a file",
                        headers={"Content-Type": "text/plain"}).json()["attachment"]

    response = api.get("/api/export")
    assert response.status_code == 200, response.text
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    names = archive.namelist()
    match = [name for name in names if uploaded["id"] in name]
    assert match, f"uploaded file missing from export: {names}"
    assert archive.read(match[0]) == b"hello from a file"


def test_concurrent_writers_do_not_fail_with_a_table_lock(tmp_path):
    """A background write and a foreground write, over and over.

    The retry is bounded, so this is not a licence to hammer the database: it
    asserts that ordinary concurrency stops raising, not that any load works.
    """
    path = str(tmp_path / "concurrent.db")
    memory = LayeredMemory(db_path=path)
    errors: list[str] = []
    stop = threading.Event()

    def writer(tag: str) -> None:
        index = 0
        while not stop.is_set():
            try:
                # dedupe off: this test is about locking, and near-identical
                # sentences are legitimately merged into one row.
                memory.add("personal", f"{tag} fact number {index}", dedupe=False)
                index += 1
            except Exception as exc:  # noqa: BLE001 - recorded, then asserted
                errors.append(f"{type(exc).__name__}: {exc}")
                return

    threads = [threading.Thread(target=writer, args=(name,)) for name in ("a", "b")]
    for thread in threads:
        thread.start()
    stop.wait(1.0)
    stop.set()
    for thread in threads:
        thread.join(timeout=5)

    assert errors == []
    assert memory.stats()["total"] > 5
    memory.close()


def test_memory_worker_and_settings_writes_coexist(client):
    """The exact sequence that used to raise: learn in the background while a
    settings write is in flight."""
    api, state = client
    cid = api.post("/api/conversations", json={"title": "c"}).json()["id"]
    for index in range(6):
        api.post("/api/chat/sync", json={
            "message": f"My name is Ada and I prefer tool {index}.",
            "conversation_id": cid})
        api.patch("/api/memory/settings", json={"memory_retrieval_limit": 3 + index % 3})
    state.memory_service.drain(5)
    assert state.memory.stats()["total"] >= 1
