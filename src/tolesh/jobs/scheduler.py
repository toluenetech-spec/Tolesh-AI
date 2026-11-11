"""Persistent background jobs.

A job is a row, not a thread. The scheduler claims due rows, runs them on a
small pool, and writes the outcome back, so restarting Tolesh does not lose a
schedule and does not silently re-run a task that already finished.

What it guarantees:
  * at most ``workers`` jobs at once, and a due job is claimed atomically
    (``UPDATE ... WHERE status='scheduled'``) so two claimers cannot both run it;
  * pause/resume/cancel are state transitions the worker respects, and a
    running job is asked to stop through a cancellation token;
  * every attempt leaves a row in ``job_runs`` and, if it changed anything,
    a notification;
  * a recurring job schedules its next run from the *finish* time, so a slow
    job does not pile up behind itself;
  * a calendar job ("cron") schedules its next run from the next matching
    minute instead, because the calendar does not care when the last one
    finished — and it cannot fire twice in the same minute.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from typing import Any, Callable

from .._sqlite import connect
from .cron import CronError, CronExpression

TERMINAL = {"done", "failed", "cancelled"}
KINDS = ("agent", "prompt")


class JobError(RuntimeError):
    pass


class JobScheduler:
    def __init__(self, db_path: str = ":memory:", *,
                 executor: Callable[[dict[str, Any], Callable[[str, dict], None]], dict] | None = None,
                 workers: int = 2, tick: float = 0.5, autostart: bool = True):
        self._conn = connect(db_path)
        self._lock = threading.RLock()
        self._executor = executor
        self._workers = max(1, workers)
        self._tick = max(0.1, tick)
        self._stop = threading.Event()
        self._cancels: dict[str, threading.Event] = {}
        self._thread: threading.Thread | None = None
        self._running: set[str] = set()
        self._cron_cache: dict[str, CronExpression] = {}
        self._create_tables()
        if autostart:
            self.start()

    def _create_tables(self) -> None:
        with self._lock:
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, name TEXT, kind TEXT, goal TEXT,
                    schedule TEXT, interval_sec REAL, next_run_at REAL,
                    status TEXT, created_at REAL, updated_at REAL,
                    last_run_at REAL, last_result TEXT, error TEXT,
                    run_count INTEGER, max_runs INTEGER, notify INTEGER,
                    conversation_id TEXT
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS job_runs (
                    id TEXT PRIMARY KEY, job_id TEXT, started_at REAL,
                    finished_at REAL, status TEXT, result TEXT, error TEXT,
                    progress TEXT
                )"""
            )
            self._conn.execute(
                """CREATE TABLE IF NOT EXISTS notifications (
                    id TEXT PRIMARY KEY, title TEXT, body TEXT, job_id TEXT,
                    created_at REAL, read INTEGER
                )"""
            )
            # Additive migration: progress used to share the result column,
            # which meant the final outcome erased the steps that produced it.
            columns = {r[1] for r in self._conn.execute("PRAGMA table_info(job_runs)")}
            if "progress" not in columns:
                self._conn.execute("ALTER TABLE job_runs ADD COLUMN progress TEXT")
            # Additive migration: calendar schedules. Existing rows keep
            # schedule='once' or 'interval' and a NULL cron, so they behave
            # exactly as before.
            job_columns = {r[1] for r in self._conn.execute("PRAGMA table_info(jobs)")}
            if "cron" not in job_columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN cron TEXT")
            if "last_fired_minute" not in job_columns:
                self._conn.execute("ALTER TABLE jobs ADD COLUMN last_fired_minute INTEGER")
            self._conn.commit()

    # ------------------------------------------------------------ validation
    @staticmethod
    def _checked_cron(expression: str | None) -> str:
        """Return the canonical form of ``expression``, or refuse it."""
        if expression is None or not str(expression).strip():
            raise JobError("A calendar schedule needs a cron expression.")
        try:
            return CronExpression(str(expression)).expression
        except CronError as exc:
            raise JobError(str(exc)) from exc

    def _cron(self, expression: str) -> CronExpression:
        parsed = self._cron_cache.get(expression)
        if parsed is None:
            parsed = CronExpression(expression)
            if len(self._cron_cache) > 200:
                self._cron_cache.clear()
            self._cron_cache[expression] = parsed
        return parsed

    # ------------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name="tolesh-jobs",
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    def close(self) -> None:
        self.stop()
        with self._lock:
            self._conn.close()

    # ------------------------------------------------------------------ CRUD
    def create(self, name: str, *, goal: str, kind: str = "agent",
               schedule: str = "once", interval_sec: float | None = None,
               cron: str | None = None,
               next_run_at: float | None = None, max_runs: int | None = None,
               notify: bool = True,
               conversation_id: str | None = None) -> dict[str, Any]:
        if kind not in KINDS:
            raise JobError("A job kind must be one of: " + ", ".join(KINDS))
        if not (goal or "").strip():
            raise JobError("A job needs a goal.")
        if schedule not in ("once", "interval", "cron"):
            raise JobError("A schedule must be 'once', 'interval' or 'cron'.")
        if schedule == "interval" and (not interval_sec or interval_sec < 5):
            raise JobError("A recurring job needs an interval of at least 5 seconds.")
        if schedule == "cron":
            cron = self._checked_cron(cron)
        else:
            cron = None
        now = time.time()
        job_id = f"job-{secrets.token_hex(5)}"
        if next_run_at is not None:
            start_at = next_run_at
        elif schedule == "cron":
            # The first run is the next matching minute, not "now".
            start_at = CronExpression(cron).next_after_ts(now)
        else:
            start_at = now
        with self._lock:
            self._conn.execute(
                """INSERT INTO jobs (id, name, kind, goal, schedule, interval_sec,
                    next_run_at, status, created_at, updated_at, last_run_at,
                    last_result, error, run_count, max_runs, notify,
                    conversation_id, cron)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (job_id, (name or goal)[:120], kind, goal.strip(), schedule,
                 interval_sec, start_at, "scheduled", now, now, None, "", "",
                 0, max_runs, 1 if notify else 0, conversation_id or "", cron))
            self._conn.commit()
        return self.get(job_id) or {}

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT id, name, kind, goal, schedule, interval_sec, next_run_at,
                          status, created_at, updated_at, last_run_at, last_result,
                          error, run_count, max_runs, notify, conversation_id,
                          cron, last_fired_minute
                   FROM jobs WHERE id=?""", (job_id,)).fetchone()
        return self._row(row) if row else None

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, name, kind, goal, schedule, interval_sec, next_run_at,
                          status, created_at, updated_at, last_run_at, last_result,
                          error, run_count, max_runs, notify, conversation_id,
                          cron, last_fired_minute
                   FROM jobs ORDER BY created_at DESC LIMIT ?""",
                (max(1, min(limit, 200)),)).fetchall()
        return [self._row(r) for r in rows]

    def update(self, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
        allowed = {"name", "goal", "schedule", "interval_sec", "cron",
                   "next_run_at", "status", "max_runs", "notify"}
        fields = {k: v for k, v in patch.items() if k in allowed}
        if not fields:
            raise JobError("No supported fields to update.")
        if "status" in fields and fields["status"] not in (
                "scheduled", "paused", "cancelled", "done"):
            raise JobError("Status must be scheduled, paused, cancelled or done.")
        if "schedule" in fields and fields["schedule"] not in (
                "once", "interval", "cron"):
            raise JobError("A schedule must be 'once', 'interval' or 'cron'.")
        switching = fields.get("schedule")
        if "cron" in fields or switching == "cron":
            current = (self.get(job_id) or {}).get("cron")
            expression = fields.get("cron", current)
            if expression is None:
                raise JobError("A calendar schedule needs a cron expression.")
            fields["cron"] = self._checked_cron(expression)
            if "next_run_at" not in fields:
                # The next run comes from the calendar, never from "now".
                fields["next_run_at"] = CronExpression(fields["cron"]).next_after_ts(
                    time.time())
        elif switching in ("once", "interval"):
            fields["cron"] = None
        fields["updated_at"] = time.time()
        with self._lock:
            if self.get(job_id) is None:
                raise JobError("Unknown job.")
            columns = ", ".join(f"{k}=?" for k in fields)
            self._conn.execute(f"UPDATE jobs SET {columns} WHERE id=?",
                               (*fields.values(), job_id))
            self._conn.commit()
        return self.get(job_id) or {}

    def delete(self, job_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM jobs WHERE id=?", (job_id,))
            self._conn.execute("DELETE FROM job_runs WHERE job_id=?", (job_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def pause(self, job_id: str) -> dict[str, Any]:
        return self.update(job_id, {"status": "paused"})

    def resume(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise JobError("Unknown job.")
        if job["status"] in TERMINAL and job["schedule"] == "once":
            raise JobError("This one-off job already finished; create a new one.")
        next_at = job["next_run_at"] or time.time()
        if job["schedule"] == "cron" and job.get("cron"):
            # Never resume into the past: re-anchor on the calendar.
            next_at = CronExpression(job["cron"]).next_after_ts(time.time())
        return self.update(job_id, {"status": "scheduled", "next_run_at": next_at})

    def cancel(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise JobError("Unknown job.")
        token = self._cancels.setdefault(job_id, threading.Event())
        token.set()
        return self.update(job_id, {"status": "cancelled"})

    def run_now(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job is None:
            raise JobError("Unknown job.")
        result = self.update(job_id, {"status": "scheduled",
                                      "next_run_at": time.time()})
        # An explicit run is not a scheduler tick, so it may fire in a minute
        # the scheduler already used.
        with self._lock:
            self._conn.execute(
                "UPDATE jobs SET last_fired_minute=NULL WHERE id=?", (job_id,))
            self._conn.commit()
        return result

    # ------------------------------------------------------------------ runs
    def runs(self, job_id: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT id, job_id, started_at, finished_at, status, result, error,
                          progress
                   FROM job_runs WHERE job_id=? ORDER BY started_at DESC LIMIT ?""",
                (job_id, max(1, min(limit, 100)))).fetchall()
        return [{"id": r[0], "job_id": r[1], "started_at": r[2],
                 "finished_at": r[3], "status": r[4], "result": r[5],
                 "error": r[6], "progress": r[7]} for r in rows]

    def notifications(self, unread_only: bool = False, limit: int = 50) -> list[dict[str, Any]]:
        sql = ("SELECT id, title, body, job_id, created_at, read FROM notifications")
        if unread_only:
            sql += " WHERE read=0"
        sql += " ORDER BY created_at DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (max(1, min(limit, 200)),)).fetchall()
        return [{"id": r[0], "title": r[1], "body": r[2], "job_id": r[3],
                 "created_at": r[4], "read": bool(r[5])} for r in rows]

    def mark_read(self, notification_id: str) -> bool:
        with self._lock:
            cur = self._conn.execute("UPDATE notifications SET read=1 WHERE id=?",
                                     (notification_id,))
            self._conn.commit()
        return cur.rowcount > 0

    def mark_all_read(self) -> int:
        with self._lock:
            cur = self._conn.execute("UPDATE notifications SET read=1 WHERE read=0")
            self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ loop
    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick_once()
            except Exception:
                # The scheduler must survive a bad job row.
                pass
            self._stop.wait(self._tick)

    def _tick_once(self) -> None:
        if len(self._running) >= self._workers:
            return
        job = self._claim()
        if job is None:
            return
        thread = threading.Thread(target=self._execute, args=(job,),
                                  name=f"tolesh-job-{job['id']}", daemon=True)
        thread.start()

    def _claim(self) -> dict[str, Any] | None:
        now = time.time()
        minute = int(now // 60)
        with self._lock:
            # A calendar job that already fired in this minute is not due again,
            # however many ticks the loop manages inside it.
            row = self._conn.execute(
                """SELECT id FROM jobs
                   WHERE status='scheduled' AND next_run_at<=?
                     AND (schedule<>'cron' OR last_fired_minute IS NULL
                          OR last_fired_minute<>?)
                   ORDER BY next_run_at ASC LIMIT 1""", (now, minute)).fetchone()
            if row is None:
                return None
            job_id = row[0]
            if job_id in self._running:
                return None
            updated = self._conn.execute(
                """UPDATE jobs SET status='running', last_run_at=?, updated_at=?,
                          last_fired_minute=?
                   WHERE id=? AND status='scheduled'""",
                (now, now, minute, job_id))
            self._conn.commit()
            if updated.rowcount != 1:
                return None
            self._running.add(job_id)
        return self.get(job_id)

    def _execute(self, job: dict[str, Any]) -> None:
        run_id = f"run-{secrets.token_hex(5)}"
        started = time.time()
        token = self._cancels.setdefault(job["id"], threading.Event())
        token.clear()
        with self._lock:
            self._conn.execute(
                "INSERT INTO job_runs (id, job_id, started_at, status) VALUES (?,?,?,?)",
                (run_id, job["id"], started, "running"))
            self._conn.commit()

        def progress(message: str, data: dict[str, Any] | None = None) -> None:
            with self._lock:
                self._conn.execute(
                    "UPDATE job_runs SET progress=? WHERE id=?",
                    (json.dumps({"message": message, **(data or {})}, default=str)[:4000],
                     run_id))
                self._conn.commit()

        status, result, error = "failed", "", ""
        try:
            if self._executor is None:
                raise JobError("No executor is configured, so jobs cannot run.")
            outcome = self._executor(job, progress) or {}
            result = str(outcome.get("result") or "")[:8000]
            error = str(outcome.get("error") or "")[:2000]
            status = "cancelled" if token.is_set() else (
                "done" if not error else "failed")
        except Exception as exc:
            error = str(exc)[:2000]
            status = "cancelled" if token.is_set() else "failed"
        finally:
            finished = time.time()
            with self._lock:
                self._conn.execute(
                    "UPDATE job_runs SET finished_at=?, status=?, result=?, error=? "
                    "WHERE id=?", (finished, status, result, error, run_id))
                self._conn.commit()
            self._finish(job, status, result, error)
            self._running.discard(job["id"])

    def _finish(self, job: dict[str, Any], status: str, result: str,
                error: str) -> None:
        now = time.time()
        runs = int(job.get("run_count") or 0) + 1
        with self._lock:
            current = self.get(job["id"]) or {}
        cancelled = current.get("status") == "cancelled"
        recurring = job.get("schedule") == "interval" and job.get("interval_sec")
        calendar = job.get("schedule") == "cron" and job.get("cron")
        max_runs = job.get("max_runs")
        exhausted = bool(max_runs and runs >= int(max_runs))
        calendar_error = ""

        if cancelled or status == "cancelled":
            new_status = "cancelled"
            next_at = None
        elif calendar and not exhausted and status in ("done", "failed"):
            # The calendar decides, not the finish time: a job that took four
            # minutes still fires on the next matching minute.
            try:
                next_at = self._cron(str(job["cron"])).next_after_ts(now)
                new_status = "scheduled"
            except CronError as exc:
                calendar_error = str(exc)
                new_status = "paused"
                next_at = None
        elif recurring and not exhausted and status in ("done", "failed"):
            new_status = "scheduled"
            next_at = now + float(job["interval_sec"])
        else:
            new_status = status if status in ("done", "failed") else "done"
            next_at = None
        if calendar_error:
            error = (error + " " if error else "") + calendar_error

        with self._lock:
            self._conn.execute(
                """UPDATE jobs SET status=?, updated_at=?, last_result=?, error=?,
                   run_count=?, next_run_at=? WHERE id=?""",
                (new_status, now, result, error, runs, next_at, job["id"]))
            self._conn.commit()
        if job.get("notify"):
            title = f"{job['name']}: {new_status}"
            body = (error or result or "The job finished.")[:500]
            self._notify(title, body, job["id"])

    def _notify(self, title: str, body: str, job_id: str) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO notifications (id, title, body, job_id, created_at, read) "
                "VALUES (?,?,?,?,?,0)",
                (f"ntf-{secrets.token_hex(5)}", title[:200], body, job_id, time.time()))
            self._conn.commit()

    @staticmethod
    def _row(row: Any) -> dict[str, Any]:
        return {"id": row[0], "name": row[1], "kind": row[2], "goal": row[3],
                "schedule": row[4], "interval_sec": row[5], "next_run_at": row[6],
                "status": row[7], "created_at": row[8], "updated_at": row[9],
                "last_run_at": row[10], "last_result": row[11], "error": row[12],
                "run_count": row[13], "max_runs": row[14], "notify": bool(row[15]),
                "conversation_id": row[16], "cron": row[17],
                "last_fired_minute": row[18]}
