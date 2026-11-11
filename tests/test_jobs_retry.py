"""Failure recovery: attempt counters, backoff, and giving up loudly.

A recurring job used to reschedule itself after every failure with no limit and
no delay, so a permanently broken task retried forever and the only evidence
was a row in the Jobs tab. These tests pin down what replaces that.
"""

from __future__ import annotations

import time

import pytest

from tolesh.jobs import JobError, JobScheduler
from tolesh.jobs.scheduler import (DEFAULT_BACKOFF_SEC, DEFAULT_MAX_ATTEMPTS,
                                   MAX_BACKOFF_SEC)


def wait_until(predicate, timeout: float = 6.0, interval: float = 0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Flaky:
    """An executor that fails ``failures`` times and then succeeds."""

    def __init__(self, failures: int = 99, error: str = "upstream 503"):
        self.remaining = failures
        self.error = error
        self.calls = 0

    def __call__(self, job, progress):
        self.calls += 1
        if self.remaining > 0:
            self.remaining -= 1
            raise RuntimeError(self.error)
        return {"result": "recovered"}


def make(tmp_path, executor, name="jobs.db"):
    return JobScheduler(str(tmp_path / name), executor=executor, autostart=False)


def run_once(sched, job_id, timeout=6.0):
    """Make the job due, tick once, and wait for the attempt to finish."""
    sched.update(job_id, {"status": "scheduled", "next_run_at": time.time() - 1})
    before = sched.get(job_id)["run_count"]
    sched._tick_once()
    assert wait_until(lambda: sched.get(job_id)["run_count"] > before
                      or sched.get(job_id)["status"] != "running"), "the attempt never ran"
    assert wait_until(lambda: sched.get(job_id)["status"] != "running")
    return sched.get(job_id)


# --------------------------------------------------------------- the counter
def test_a_transient_failure_is_retried_sooner_than_the_next_run(tmp_path):
    executor = Flaky(failures=1)
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Hourly", goal="report", schedule="interval",
                           interval_sec=3600, backoff_sec=60)
        stored = run_once(sched, job["id"])
        assert stored["status"] == "scheduled"
        assert stored["attempts"] == 1
        assert stored["error"]
        # The retry is the backoff, not the hour-long interval.
        delay = stored["next_run_at"] - time.time()
        assert 55 < delay < 70, delay
    finally:
        sched.close()


def test_backoff_doubles_and_is_capped(tmp_path):
    executor = Flaky()
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Backoff", goal="x", schedule="interval",
                           interval_sec=86400, backoff_sec=60, max_attempts=10)
        delays = []
        for _ in range(4):
            stored = run_once(sched, job["id"])
            delays.append(round(stored["next_run_at"] - time.time()))
        assert stored["attempts"] == 4
        assert 55 <= delays[0] <= 65, delays
        assert 115 <= delays[1] <= 130, delays
        assert 235 <= delays[2] <= 250, delays
        assert 475 <= delays[3] <= 490, delays
    finally:
        sched.close()


def test_the_backoff_never_exceeds_the_cap(tmp_path):
    executor = Flaky()
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Slow", goal="x", schedule="interval",
                           interval_sec=86400, backoff_sec=1800, max_attempts=20)
        stored = run_once(sched, job["id"])
        assert stored["attempts"] == 1
        delay = stored["next_run_at"] - time.time()
        assert delay <= MAX_BACKOFF_SEC + 2, delay
    finally:
        sched.close()


def test_a_success_resets_the_consecutive_failure_count(tmp_path):
    executor = Flaky(failures=2)
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Recovers", goal="x", schedule="interval",
                           interval_sec=3600, backoff_sec=5, max_attempts=3)
        assert run_once(sched, job["id"])["attempts"] == 1
        assert run_once(sched, job["id"])["attempts"] == 2
        stored = run_once(sched, job["id"])
        assert stored["status"] == "scheduled"
        assert stored["attempts"] == 0
        assert stored["last_result"] == "recovered"
    finally:
        sched.close()


def test_failures_do_not_accumulate_across_successes(tmp_path):
    """Three failures spread over three separate incidents is not one outage."""
    executor = Flaky(failures=0)
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Stable", goal="x", schedule="interval",
                           interval_sec=3600, max_attempts=2)
        for _ in range(3):
            executor.remaining = 1
            assert run_once(sched, job["id"])["attempts"] == 1
            assert run_once(sched, job["id"])["attempts"] == 0
        assert sched.get(job["id"])["status"] == "scheduled"
    finally:
        sched.close()


# ------------------------------------------------------------------ giving up
def test_the_job_pauses_at_the_failure_limit(tmp_path):
    executor = Flaky()
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Doomed", goal="x", schedule="interval",
                           interval_sec=3600, backoff_sec=1, max_attempts=3)
        for attempt in (1, 2):
            stored = run_once(sched, job["id"])
            assert stored["status"] == "scheduled", stored
            assert stored["attempts"] == attempt
        stored = run_once(sched, job["id"])
        assert stored["status"] == "paused"
        assert stored["attempts"] == 3
        assert stored["next_run_at"] is None
        # Paused means paused: no further ticks run it.
        sched.update(job["id"], {"next_run_at": time.time() - 1})
        sched._tick_once()
        time.sleep(0.3)
        assert executor.calls == 3
        assert sched.get(job["id"])["run_count"] == 3
    finally:
        sched.close()


def test_giving_up_produces_a_notification_even_when_notifications_are_off(tmp_path):
    executor = Flaky(error="the provider is down")
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Quiet", goal="x", schedule="interval", interval_sec=3600,
                           backoff_sec=1, max_attempts=1, notify=False)
        stored = run_once(sched, job["id"])
        assert stored["status"] == "paused"
        notes = sched.notifications()
        assert notes, "a job that gave up must say so"
        assert "paused after 1 failed attempts" in notes[0]["title"]
        assert "provider is down" in notes[0]["body"]
    finally:
        sched.close()


def test_a_one_off_job_is_not_retried(tmp_path):
    executor = Flaky()
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Once", goal="x", next_run_at=time.time() - 1)
        stored = run_once(sched, job["id"])
        assert stored["status"] == "failed"
        assert stored["attempts"] == 1
        assert stored["next_run_at"] is None
        sched._tick_once()
        time.sleep(0.2)
        assert executor.calls == 1
    finally:
        sched.close()


def test_resuming_after_giving_up_starts_the_count_again(tmp_path):
    executor = Flaky(failures=3)
    sched = make(tmp_path, executor)
    try:
        job = sched.create("Retry me", goal="x", schedule="interval",
                           interval_sec=3600, backoff_sec=1, max_attempts=2)
        assert run_once(sched, job["id"])["attempts"] == 1
        assert run_once(sched, job["id"])["status"] == "paused"
        resumed = sched.resume(job["id"])
        assert resumed["status"] == "scheduled"
        assert resumed["attempts"] == 0
        # Two fresh attempts: the third call still fails, the fourth works.
        assert run_once(sched, job["id"])["attempts"] == 1
        stored = run_once(sched, job["id"])
        assert stored["status"] == "scheduled"
        assert stored["attempts"] == 0
        assert stored["last_result"] == "recovered"
    finally:
        sched.close()


def test_the_attempt_count_survives_a_restart(tmp_path):
    executor = Flaky()
    first = make(tmp_path, executor)
    job = first.create("Durable", goal="x", schedule="interval", interval_sec=3600,
                       backoff_sec=60, max_attempts=5)
    assert run_once(first, job["id"])["attempts"] == 1
    first.close()

    second = make(tmp_path, executor)
    try:
        stored = second.get(job["id"])
        assert stored["attempts"] == 1
        assert stored["max_attempts"] == 5
        assert run_once(second, job["id"])["attempts"] == 2
    finally:
        second.close()


# ----------------------------------------------------------------- calendar
def test_a_calendar_job_retries_but_not_past_its_next_run(tmp_path):
    executor = Flaky()
    sched = make(tmp_path, executor)
    try:
        # Fires every minute, with a backoff far longer than a minute.
        job = sched.create("EveryMinute", goal="x", schedule="cron",
                           cron="* * * * *", backoff_sec=3600, max_attempts=5)
        stored = run_once(sched, job["id"])
        assert stored["attempts"] == 1
        assert stored["status"] == "scheduled"
        # The retry is clamped to the next calendar minute, so no occurrence
        # is skipped because of a long backoff.
        assert stored["next_run_at"] <= time.time() + 61
        assert stored["next_run_at"] % 60 == 0
    finally:
        sched.close()


# ---------------------------------------------------------------------- API
@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from tolesh.server import create_app
    from tolesh.server.app import AppState

    monkeypatch.setenv("TOLESH_MCP_PATH", str(tmp_path / "mcp.json"))
    state = AppState(in_memory=True)
    app = create_app(state=state, web_dir=str(tmp_path), serve_static=False)
    with TestClient(app) as test_client:
        test_client.state = state  # type: ignore[attr-defined]
        yield test_client
    state.close()


def test_api_accepts_and_reports_the_retry_settings(client):
    created = client.post("/api/jobs", json={
        "name": "Configured", "goal": "x", "kind": "prompt",
        "schedule": "interval", "interval_sec": 600,
        "max_attempts": 5, "backoff_sec": 30}).json()["job"]
    assert created["max_attempts"] == 5
    assert created["backoff_sec"] == 30
    assert created["attempts"] == 0

    default = client.post("/api/jobs", json={
        "name": "Default", "goal": "y", "kind": "prompt"}).json()["job"]
    assert default["max_attempts"] == DEFAULT_MAX_ATTEMPTS
    assert default["backoff_sec"] == DEFAULT_BACKOFF_SEC


def test_api_refuses_nonsense_retry_settings(client):
    for body in ({"max_attempts": 0}, {"max_attempts": 21}, {"backoff_sec": 0}):
        response = client.post("/api/jobs", json={
            "name": "Bad", "goal": "x", "schedule": "interval",
            "interval_sec": 600, **body})
        assert response.status_code in (400, 422), (body, response.text)


def test_patching_the_limit_is_validated(client):
    job = client.post("/api/jobs", json={
        "name": "Patch", "goal": "x", "schedule": "interval",
        "interval_sec": 600}).json()["job"]
    ok = client.patch(f"/api/jobs/{job['id']}", json={"max_attempts": 7})
    assert ok.status_code == 200
    assert ok.json()["job"]["max_attempts"] == 7
    bad = client.patch(f"/api/jobs/{job['id']}", json={"max_attempts": 99})
    assert bad.status_code in (400, 422)
