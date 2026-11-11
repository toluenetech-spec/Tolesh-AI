"""Scheduler reliability: races, restarts, clock changes, boundaries.

The cron parser has its own tests. These are about what happens when the
scheduler is wrong in the ways that actually lose work: two ticks claiming the
same job, a process killed mid-run, an application that was closed for three
hours, a job that outlives its own interval, and the two days a year when the
clock moves.
"""

from __future__ import annotations

import os
import threading
import time

import pytest

from tolesh.jobs import JobError, JobScheduler
from tolesh.jobs.cron import CronError, CronExpression
from tolesh.jobs.scheduler import DEFAULT_MAX_ATTEMPTS


def wait_until(predicate, timeout: float = 6.0, interval: float = 0.02):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


class Counter:
    def __init__(self, sleep: float = 0.0):
        self.calls = 0
        self.sleep = sleep
        self.lock = threading.Lock()

    def __call__(self, job, progress):
        with self.lock:
            self.calls += 1
        if self.sleep:
            time.sleep(self.sleep)
        return {"result": "done"}


def make(tmp_path, executor, *, name="jobs.db", workers=2, tick=0.5):
    return JobScheduler(str(tmp_path / name), executor=executor, autostart=False,
                        workers=workers, tick=tick)


def _mark_running(sched, job_id):
    """The exact state a process leaves behind when it is killed mid-run."""
    sched._conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job_id,))
    sched._conn.commit()


# ------------------------------------------------------------------- races
def test_concurrent_ticks_never_run_the_same_job_twice(tmp_path):
    """The claim is one UPDATE guarded by status, not a read then a write."""
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("once", goal="do a thing", schedule="once",
                       next_run_at=time.time() - 1)

    threads = [threading.Thread(target=sched._tick_once) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert wait_until(lambda: sched.get(job["id"])["status"] != "running")
    assert seen.calls == 1
    sched.close()


def test_two_schedulers_on_one_database_do_not_both_run_a_job(tmp_path):
    """A second process must lose the claim, not duplicate the work."""
    first_calls = Counter(sleep=0.05)
    second_calls = Counter(sleep=0.05)
    path = str(tmp_path / "shared.db")
    one = JobScheduler(path, executor=first_calls, autostart=False)
    two = JobScheduler(path, executor=second_calls, autostart=False)
    job = one.create("once", goal="x", schedule="once",
                     next_run_at=time.time() - 1)

    one._tick_once()
    two._tick_once()

    assert wait_until(lambda: one.get(job["id"])["status"] != "running")
    assert first_calls.calls + second_calls.calls == 1
    one.close()
    two.close()


def test_a_calendar_job_fires_once_per_minute_however_often_it_is_ticked(tmp_path):
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("every minute", goal="x", schedule="cron",
                       cron="* * * * *")
    sched.update(job["id"], {"next_run_at": time.time() - 1})

    for _ in range(8):
        sched._tick_once()
        time.sleep(0.02)

    assert wait_until(lambda: sched.get(job["id"])["status"] != "running")
    assert seen.calls == 1
    sched.close()


# ---------------------------------------------------------------- restarts
def test_a_job_left_running_by_a_crash_is_rescheduled(tmp_path):
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("recurring", goal="x", schedule="interval", interval_sec=5)
    # Simulate the process dying mid-run: the row says running, no thread exists.
    _mark_running(sched, job["id"])
    sched.close()

    fresh = make(tmp_path, seen)
    recovered = fresh.get(job["id"])

    assert recovered["status"] == "scheduled"
    assert recovered["attempts"] == 1
    assert "restart" in (recovered["error"] or "").lower()
    assert fresh.recover_interrupted() == 0, "recovery is not idempotent"
    fresh.close()


def test_the_run_row_of_an_interrupted_job_is_closed(tmp_path):
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("recurring", goal="x", schedule="interval", interval_sec=5)
    sched._conn.execute(
        "INSERT INTO job_runs (id, job_id, started_at, status) "
        "VALUES ('run-1', ?, ?, 'running')", (job["id"], time.time()))
    sched._conn.execute("UPDATE jobs SET status='running' WHERE id=?", (job["id"],))
    sched._conn.commit()
    sched.close()

    fresh = make(tmp_path, seen)
    row = fresh._conn.execute(
        "SELECT status, error FROM job_runs WHERE id='run-1'").fetchone()

    assert row[0] == "interrupted"
    assert "stopped" in row[1]
    fresh.close()


def test_a_job_that_keeps_killing_the_process_is_paused_not_retried_forever(tmp_path):
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("poison", goal="x", schedule="interval", interval_sec=5,
                       max_attempts=2)
    _mark_running(sched, job["id"])
    sched.close()

    second = make(tmp_path, seen)
    _mark_running(second, job["id"])
    second.close()

    third = make(tmp_path, seen)
    paused = third.get(job["id"])

    assert paused["status"] == "paused"
    assert paused["attempts"] >= 2
    assert "Paused" in paused["error"]
    third.close()


def test_a_job_missed_while_tolesh_was_offline_runs_once_not_once_per_miss(tmp_path):
    """Three hours closed is one catch-up run, not three hours of backlog."""
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("hourly", goal="x", schedule="cron", cron="0 * * * *")
    sched.update(job["id"], {"next_run_at": time.time() - 3 * 3600})
    sched.close()

    fresh = make(tmp_path, seen)
    fresh._tick_once()

    assert wait_until(lambda: seen.calls == 1)
    assert wait_until(lambda: fresh.get(job["id"])["status"] == "scheduled")
    fresh._tick_once()
    time.sleep(0.1)
    assert seen.calls == 1, "the backlog was replayed"
    fresh.close()


def test_a_restart_preserves_the_attempt_count(tmp_path):
    seen = Counter()
    sched = make(tmp_path, seen)
    job = sched.create("x", goal="x", schedule="interval", interval_sec=60,
                       max_attempts=5)
    sched.update(job["id"], {"attempts": 3})
    sched.close()

    fresh = make(tmp_path, seen)

    assert fresh.get(job["id"])["attempts"] == 3
    fresh.close()


# ------------------------------------------------------- long-running jobs
def test_a_job_longer_than_its_interval_does_not_overlap_itself(tmp_path):
    seen = Counter(sleep=0.4)
    sched = make(tmp_path, seen, workers=2)
    job = sched.create("slow", goal="x", schedule="interval", interval_sec=5)
    sched.update(job["id"], {"next_run_at": time.time() - 1})

    sched._tick_once()
    # While it runs, another tick must not start a second copy of the same job.
    sched._tick_once()
    sched._tick_once()

    assert wait_until(lambda: sched.get(job["id"])["status"] == "scheduled")
    assert seen.calls == 1
    # The next run is scheduled from the finish, so a 0.4 s job on a 5 s
    # interval waits a full interval rather than firing immediately again.
    assert sched.get(job["id"])["next_run_at"] > time.time() + 3
    sched.close()


# ------------------------------------------------------------- boundaries
@pytest.mark.parametrize("expression,start,expected", [
    # Month-end: a 31st schedule skips February rather than firing on the 28th.
    ("0 0 31 * *", (2025, 1, 31, 12, 0), (2025, 3, 31, 0, 0)),
    # Year-end rollover.
    ("0 0 1 1 *", (2025, 12, 31, 23, 59), (2026, 1, 1, 0, 0)),
    ("59 23 31 12 *", (2025, 6, 1, 0, 0), (2025, 12, 31, 23, 59)),
    # Leap day only in a leap year.
    ("0 0 29 2 *", (2025, 3, 1, 0, 0), (2028, 2, 29, 0, 0)),
    # Month-end from the 30th.
    ("0 0 30 * *", (2025, 1, 31, 0, 0), (2025, 3, 30, 0, 0)),
])
def test_calendar_boundaries(expression, start, expected):
    import datetime as dt

    got = CronExpression(expression).next_after(dt.datetime(*start))
    assert (got.year, got.month, got.day, got.hour, got.minute) == expected


def test_a_date_that_never_exists_is_refused():
    with pytest.raises(CronError):
        CronExpression("0 0 30 2 *").next_after(__import__("datetime").datetime(2025, 1, 1))


def test_an_invalid_expression_is_refused_at_creation(tmp_path):
    sched = make(tmp_path, Counter())
    with pytest.raises(JobError):
        sched.create("bad", goal="x", schedule="cron", cron="every tuesday")
    with pytest.raises(JobError):
        sched.create("bad", goal="x", schedule="cron", cron="0 0 0 0 0")
    sched.close()


# ------------------------------------------------------------ daylight time
@pytest.fixture
def london():
    """Run a block under Europe/London, then put the process clock back."""
    previous = os.environ.get("TZ")

    def apply(zone: str = "Europe/London") -> None:
        os.environ["TZ"] = zone
        time.tzset()

    yield apply
    if previous is None:
        os.environ.pop("TZ", None)
    else:
        os.environ["TZ"] = previous
    time.tzset()


def test_the_next_run_is_always_in_the_future_across_a_fall_back(london):
    """The hour that happens twice must not produce a past 'next' run."""
    london()
    import datetime as dt

    # 2025-10-26: 02:00 BST becomes 01:00 GMT, so 01:30 occurs twice.
    first = dt.datetime(2025, 10, 26, 1, 30, tzinfo=None)
    before = first.replace(tzinfo=None)
    stamp = before.timestamp()          # the first (BST) occurrence

    expression = CronExpression("30 1 * * *")
    nxt = expression.next_after_ts(stamp)
    assert nxt > stamp

    # And from the second occurrence, the answer is still in the future.
    after_fold = stamp + 3600
    assert expression.next_after_ts(after_fold) > after_fold


def test_a_fall_back_does_not_loop_a_job(london):
    """Ten consecutive ticks across the repeated hour must not fire ten times."""
    london()
    expression = CronExpression("* * * * *")
    import datetime as dt

    stamp = dt.datetime(2025, 10, 26, 1, 30).timestamp()
    seen: list[float] = []
    for _ in range(10):
        stamp = expression.next_after_ts(stamp)
        seen.append(stamp)

    assert seen == sorted(seen)
    assert all(b > a for a, b in zip(seen, seen[1:]))


def test_a_spring_forward_still_produces_one_run(london):
    """02:30 does not exist on 2025-03-30; the job must still fire once."""
    london()
    import datetime as dt

    expression = CronExpression("30 1 * * *")
    stamp = dt.datetime(2025, 3, 30, 0, 0).timestamp()
    nxt = expression.next_after_ts(stamp)

    assert nxt > stamp
    assert expression.next_after_ts(nxt) > nxt


# ------------------------------------------------------ permanent failures
def test_a_revoked_key_is_not_retried_three_times(tmp_path):
    """A 401 will say the same thing in sixty seconds, so do not wait."""
    from tolesh.jobs.scheduler import is_permanent

    class Unauthorised:
        def __init__(self):
            self.calls = 0

        def __call__(self, job, progress):
            self.calls += 1
            raise RuntimeError("401 Unauthorized: invalid api key")

    seen = Unauthorised()
    sched = make(tmp_path, seen)
    job = sched.create("hourly", goal="x", schedule="cron", cron="0 * * * *",
                       max_attempts=5)
    sched.update(job["id"], {"next_run_at": time.time() - 1})
    sched._tick_once()
    assert wait_until(lambda: sched.get(job["id"])["status"] != "running")

    after = sched.get(job["id"])

    assert seen.calls == 1, "a permanent failure was retried"
    assert after["status"] == "paused"
    assert after["attempts"] == 1
    assert is_permanent(after["error"])
    notes = sched.notifications()
    assert any("will not be retried" in note["title"] for note in notes)
    sched.close()


def test_a_transient_failure_is_still_retried(tmp_path):
    from tolesh.jobs.scheduler import is_permanent

    assert not is_permanent("upstream 503 Service Unavailable")
    assert not is_permanent("Read timed out")
    assert not is_permanent("rate limit exceeded, retry later")
    assert not is_permanent("connection reset by peer")
    assert not is_permanent("")


def test_a_retry_delay_survives_a_restart(tmp_path):
    class AlwaysFails:
        def __call__(self, job, progress):
            raise RuntimeError("connection reset by peer")

    sched = make(tmp_path, AlwaysFails())
    job = sched.create("x", goal="x", schedule="interval", interval_sec=60,
                       max_attempts=5)
    sched.update(job["id"], {"next_run_at": time.time() - 1})
    sched._tick_once()
    assert wait_until(lambda: sched.get(job["id"])["status"] == "scheduled")
    pending = sched.get(job["id"])
    assert pending["attempts"] == 1
    assert pending["next_run_at"] > time.time() + 10
    sched.close()

    fresh = make(tmp_path, AlwaysFails())

    restored = fresh.get(job["id"])
    assert restored["attempts"] == 1
    assert abs(restored["next_run_at"] - pending["next_run_at"]) < 1
    fresh.close()
