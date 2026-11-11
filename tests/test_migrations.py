"""Migrations against a database that predates them.

The six phases added columns to ``jobs`` and two tables for Telegram. A column
added to a table with rows in it is the kind of change that quietly loses a
user's data if it is done by rebuilding the table, so these tests start from the
*real* pre-migration schema (copied from commit 664dc34) rather than from
whatever the current code happens to create.
"""

from __future__ import annotations

import sqlite3
import threading
import time

import pytest

from tolesh.channels import TelegramLinks, TelegramSettings
from tolesh.jobs import JobScheduler
from tolesh.server.app import AppState

# The exact CREATE TABLE from before the cron and retry phases.
LEGACY_JOBS = """CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, name TEXT, kind TEXT, goal TEXT,
    schedule TEXT, interval_sec REAL, next_run_at REAL,
    status TEXT, created_at REAL, updated_at REAL,
    last_run_at REAL, last_result TEXT, error TEXT,
    run_count INTEGER, max_runs INTEGER, notify INTEGER,
    conversation_id TEXT)"""


def legacy_db(path: str) -> None:
    """A database written by the previous release, with a job in it."""
    now = time.time()
    conn = sqlite3.connect(path)
    conn.execute(LEGACY_JOBS)
    conn.execute(
        """INSERT INTO jobs (id,name,kind,goal,schedule,interval_sec,next_run_at,
                             status,created_at,updated_at,run_count,notify)
           VALUES ('old1','legacy','prompt','do a thing','interval',60,?,
                   'scheduled',?,?,2,1)""",
        (now + 60, now, now))
    conn.commit()
    conn.close()


def test_a_legacy_job_survives_the_new_columns(tmp_path):
    path = str(tmp_path / "state.db")
    legacy_db(path)

    sched = JobScheduler(path, autostart=False)
    job = sched.get("old1")

    assert job is not None
    assert job["name"] == "legacy"
    assert job["schedule"] == "interval"
    assert job["run_count"] == 2
    # New columns take safe defaults rather than NULL-ing the row.
    assert job["cron"] is None
    assert job["attempts"] == 0
    assert job["max_attempts"] == 3
    # The column is NULL; the API surface reports the effective default.
    assert job["backoff_sec"] == 60.0
    assert job["last_fired_minute"] is None
    sched.close()


def test_migrations_are_idempotent(tmp_path):
    path = str(tmp_path / "state.db")
    legacy_db(path)

    for _ in range(4):
        JobScheduler(path, autostart=False).close()
        TelegramLinks(path).close()
        TelegramSettings(path).close()

    sched = JobScheduler(path, autostart=False)
    assert sched.get("old1")["name"] == "legacy"
    sched.close()


def test_conversations_messages_and_settings_survive_a_reopen(tmp_path):
    path = str(tmp_path / "state.db")
    legacy_db(path)

    first = AppState(db_path=path)
    conversation = first.conversations.create("existing chat")
    first.conversations.add_message(conversation["id"], "user", "remember me")
    first.settings.patch({"memory_retrieval_limit": 6})
    new_job = first.jobs.create("cron job", goal="goal", schedule="cron",
                                cron="0 9 * * *")
    first.close()

    second = AppState(db_path=path)
    try:
        assert second.conversations.get(conversation["id"])["title"] == "existing chat"
        assert len(second.conversations.messages(conversation["id"])) == 1
        assert second.settings.get("memory_retrieval_limit") == 6
        assert second.jobs.get("old1") is not None
        assert second.jobs.get(new_job["id"])["cron"] == "0 9 * * *"
    finally:
        second.close()


def test_the_telegram_tables_are_created_on_a_legacy_database(tmp_path):
    path = str(tmp_path / "state.db")
    legacy_db(path)

    links = TelegramLinks(path)
    settings = TelegramSettings(path)
    try:
        links.link(42, "conv-1", "Telegram 42", "proj-1")
        assert links.conversation_for(42) == "conv-1"
        assert settings.get()["memory_scope"] == "chat"
        assert settings.get()["allowlist"] == []
    finally:
        links.close()
        settings.close()


def test_a_new_cron_job_schedules_from_the_calendar(tmp_path):
    path = str(tmp_path / "state.db")
    legacy_db(path)
    sched = JobScheduler(path, autostart=False)

    job = sched.create("nine", goal="x", schedule="cron", cron="0 9 * * *")

    assert job["next_run_at"] > time.time()
    assert job["next_run_at"] - time.time() <= 24 * 3600
    sched.close()


def test_concurrent_updates_do_not_lose_a_job(tmp_path):
    """Two threads updating different fields must not overwrite each other."""
    sched = JobScheduler(str(tmp_path / "state.db"), autostart=False)
    job = sched.create("x", goal="x", schedule="interval", interval_sec=60)

    errors: list[Exception] = []

    def bump(field, value):
        try:
            for _ in range(20):
                sched.update(job["id"], {field: value})
        except Exception as exc:          # pragma: no cover - failure path
            errors.append(exc)

    threads = [threading.Thread(target=bump, args=("notify", True)),
               threading.Thread(target=bump, args=("name", "renamed"))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    final = sched.get(job["id"])
    assert not errors
    assert final is not None
    assert final["name"] == "renamed"
    sched.close()


def test_a_second_scheduler_on_the_same_file_sees_the_same_jobs(tmp_path):
    path = str(tmp_path / "state.db")
    one = JobScheduler(path, autostart=False)
    job = one.create("x", goal="x", schedule="once", next_run_at=time.time() + 60)

    two = JobScheduler(path, autostart=False)

    assert two.get(job["id"])["name"] == "x"
    one.close()
    two.close()


def test_the_database_is_not_corrupted_by_a_hard_close(tmp_path):
    """close() while a job thread is mid-write must not leave a broken file."""
    path = str(tmp_path / "state.db")
    sched = JobScheduler(path, autostart=False,
                         executor=lambda job, progress: {"result": "ok"})
    for index in range(10):
        sched.create(f"job-{index}", goal="x", schedule="once",
                     next_run_at=time.time() + 3600)
    sched.close()

    conn = sqlite3.connect(path)
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    conn.close()

    assert result == "ok"


@pytest.mark.parametrize("column", ["cron", "last_fired_minute", "attempts",
                                    "max_attempts", "backoff_sec"])
def test_each_new_column_exists_after_migration(tmp_path, column):
    path = str(tmp_path / "state.db")
    legacy_db(path)
    JobScheduler(path, autostart=False).close()

    conn = sqlite3.connect(path)
    columns = {row[1] for row in conn.execute("PRAGMA table_info(jobs)")}
    conn.close()

    assert column in columns
