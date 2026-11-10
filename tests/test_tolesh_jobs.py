"""Phase 11 — background jobs, schedules and notifications.

The scheduler is driven deterministically (``autostart=False`` plus explicit
ticks) so the tests do not depend on wall-clock luck. The API test uses the
real thread and the real executor with a scripted model.
"""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from tolesh.jobs import JobError, JobScheduler
from tolesh.server import create_app
from tolesh.server.app import AppState

from .test_tolesh_agents import ScriptedRouter, final


def wait_until(predicate, timeout: float = 6.0, interval: float = 0.05):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


@pytest.fixture
def scheduler(tmp_path):
    calls: list[str] = []

    def executor(job, progress):
        calls.append(job["goal"])
        progress("working", {"step": 1})
        return {"result": f"finished: {job['goal']}"}

    sched = JobScheduler(str(tmp_path / "jobs.db"), executor=executor, autostart=False)
    sched.calls = calls  # type: ignore[attr-defined]
    yield sched
    sched.close()


# ------------------------------------------------------------------ running
def test_a_due_job_runs_once_and_notifies(scheduler):
    job = scheduler.create("Report", goal="summarise the week", next_run_at=time.time() - 1)
    scheduler._tick_once()
    assert wait_until(lambda: scheduler.get(job["id"])["status"] == "done")

    stored = scheduler.get(job["id"])
    assert stored["run_count"] == 1
    assert "finished" in stored["last_result"]

    runs = scheduler.runs(job["id"])
    assert runs[0]["status"] == "done"
    assert "working" in runs[0]["progress"]

    notes = scheduler.notifications()
    assert notes[0]["title"].endswith("done")
    assert notes[0]["read"] is False


def test_a_future_job_does_not_run_early(scheduler):
    job = scheduler.create("Later", goal="not yet", next_run_at=time.time() + 3600)
    scheduler._tick_once()
    time.sleep(0.2)
    assert scheduler.get(job["id"])["status"] == "scheduled"
    assert scheduler.runs(job["id"]) == []


def test_a_recurring_job_reschedules_itself(scheduler):
    job = scheduler.create("Tick", goal="check", schedule="interval",
                           interval_sec=60, next_run_at=time.time() - 1)
    scheduler._tick_once()
    assert wait_until(lambda: scheduler.get(job["id"])["run_count"] == 1)
    stored = scheduler.get(job["id"])
    assert stored["status"] == "scheduled"
    assert stored["next_run_at"] > time.time() + 30


def test_max_runs_stops_a_recurring_job(scheduler):
    job = scheduler.create("Twice", goal="check", schedule="interval",
                           interval_sec=60, max_runs=1, next_run_at=time.time() - 1)
    scheduler._tick_once()
    assert wait_until(lambda: scheduler.get(job["id"])["status"] == "done")


def test_pause_prevents_a_run_and_resume_allows_it(scheduler):
    job = scheduler.create("Paused", goal="hold", schedule="interval",
                           interval_sec=60, next_run_at=time.time() - 1)
    scheduler.pause(job["id"])
    scheduler._tick_once()
    time.sleep(0.2)
    assert scheduler.get(job["id"])["run_count"] == 0

    scheduler.resume(job["id"])
    scheduler._tick_once()
    assert wait_until(lambda: scheduler.get(job["id"])["run_count"] == 1)


def test_cancel_marks_a_job_and_it_stays_cancelled(scheduler):
    job = scheduler.create("Cancel me", goal="stop", schedule="interval",
                           interval_sec=60, next_run_at=time.time() - 1)
    scheduler.cancel(job["id"])
    scheduler._tick_once()
    time.sleep(0.2)
    assert scheduler.get(job["id"])["status"] == "cancelled"
    assert scheduler.get(job["id"])["run_count"] == 0


def test_a_failing_job_is_recorded_with_its_error(tmp_path):
    def boom(job, progress):
        raise RuntimeError("the tool exploded")

    sched = JobScheduler(str(tmp_path / "jobs.db"), executor=boom, autostart=False)
    try:
        job = sched.create("Bad", goal="fail", next_run_at=time.time() - 1)
        sched._tick_once()
        assert wait_until(lambda: sched.get(job["id"])["status"] == "failed")
        stored = sched.get(job["id"])
        assert "the tool exploded" in stored["error"]
        assert "exploded" in sched.notifications()[0]["body"]
    finally:
        sched.close()


def test_notifications_can_be_marked_read(scheduler):
    job = scheduler.create("Note", goal="x", next_run_at=time.time() - 1)
    scheduler._tick_once()
    assert wait_until(lambda: scheduler.notifications())
    note = scheduler.notifications()[0]
    assert scheduler.mark_read(note["id"]) is True
    assert scheduler.notifications(unread_only=True) == []
    assert scheduler.mark_all_read() == 0


def test_jobs_survive_a_restart(tmp_path):
    first = JobScheduler(str(tmp_path / "jobs.db"), executor=lambda j, p: {}, autostart=False)
    job = first.create("Persistent", goal="keep me", next_run_at=time.time() + 3600)
    first.close()

    second = JobScheduler(str(tmp_path / "jobs.db"), executor=lambda j, p: {}, autostart=False)
    try:
        stored = second.get(job["id"])
        assert stored is not None
        assert stored["goal"] == "keep me"
        assert stored["status"] == "scheduled"
    finally:
        second.close()


# --------------------------------------------------------------- validation
def test_invalid_jobs_are_refused(scheduler):
    with pytest.raises(JobError):
        scheduler.create("No goal", goal="  ")
    with pytest.raises(JobError):
        scheduler.create("Bad kind", goal="x", kind="shell")
    with pytest.raises(JobError):
        scheduler.create("No interval", goal="x", schedule="interval")
    with pytest.raises(JobError):
        scheduler.create("Too fast", goal="x", schedule="interval", interval_sec=1)


def test_unknown_job_operations_raise(scheduler):
    with pytest.raises(JobError):
        scheduler.update("nope", {"name": "x"})
    with pytest.raises(JobError):
        scheduler.cancel("nope")
    assert scheduler.delete("nope") is False


# --------------------------------------------------------------------- API
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


def test_api_creates_runs_and_reports_a_job(client):
    client.state.router = ScriptedRouter([final("The weekly summary.")])
    created = client.post("/api/jobs", json={
        "name": "Weekly", "goal": "Summarise the week", "kind": "agent",
        "run_at": time.time() - 1, "notify": True})
    assert created.status_code == 200, created.text
    job_id = created.json()["job"]["id"]

    assert wait_until(
        lambda: client.get(f"/api/jobs/{job_id}").json()["job"]["status"] == "done",
        timeout=10)
    detail = client.get(f"/api/jobs/{job_id}").json()
    assert detail["runs"][0]["status"] == "done"
    assert "weekly summary" in detail["job"]["last_result"]

    notes = client.get("/api/notifications").json()
    assert notes["unread"] >= 1
    client.post(f"/api/notifications/{notes['notifications'][0]['id']}/read")
    assert client.get("/api/notifications?unread=true").json()["unread"] == 0


def test_api_pause_resume_and_delete(client):
    created = client.post("/api/jobs", json={
        "goal": "later", "schedule": "interval", "interval_sec": 600,
        "run_at": time.time() + 3600}).json()["job"]
    assert client.post(f"/api/jobs/{created['id']}/pause").json()["job"]["status"] == "paused"
    assert client.post(f"/api/jobs/{created['id']}/resume").json()["job"]["status"] == "scheduled"
    assert client.delete(f"/api/jobs/{created['id']}").json()["status"] == "ok"
    assert client.get(f"/api/jobs/{created['id']}").status_code == 404


def test_api_validates_a_bad_schedule(client):
    response = client.post("/api/jobs", json={"goal": "x", "schedule": "interval"})
    assert response.status_code == 400
    assert "interval" in response.json()["detail"]


def test_api_unknown_job_is_404(client):
    assert client.get("/api/jobs/nope").status_code == 404
    assert client.post("/api/jobs/nope/pause").status_code == 404
    assert client.delete("/api/jobs/nope").status_code == 404
    assert client.get("/api/jobs/nope/runs").status_code == 404
