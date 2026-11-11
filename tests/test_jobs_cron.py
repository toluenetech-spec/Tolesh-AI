"""Calendar (cron) schedules — the matcher, the search, and the scheduler.

The reference implementation at the bottom of this file is deliberately naive
and shares no code with ``tolesh.jobs.cron``: it expands each field into a set
and checks membership. Comparing the two over ten thousand random minutes is
what makes "the parser is correct" a measured claim rather than a hoped one.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta

import pytest

from tolesh.jobs import JobError, JobScheduler
from tolesh.jobs.cron import CronError, CronExpression, next_after, parse


# --------------------------------------------------------------- parsing
@pytest.mark.parametrize("expression", [
    "* * * * *", "0 9 * * *", "*/15 * * * *", "0 0 1 1 *",
    "0 9 * * mon-fri", "0 9 * * MON-FRI", "0 12 * jan,jul *",
    "30 6 1-7 * 1", "0 0 * * 7", "0 0 L * *", "0 0 * * 5L",
    "0 0 15W * *", "0 0 * * 5#3", "0 0 * * 0#1", "%15 * * * *",
    "5/10 * * * *", "@daily", "@hourly", "@weekly", "@monthly", "@yearly",
    "@annually", "@midnight",
])
def test_valid_expressions_parse(expression):
    assert parse(expression).expression


@pytest.mark.parametrize("expression", [
    "", "   ", "* * * *", "* * * * * *", "60 * * * *", "* 24 * * *",
    "* * 0 * *", "* * 32 * *", "* * * 13 *", "* * * * 8", "*/0 * * * *",
    "1-5-7 * * * *", "5-1 * * * *", "abc * * * *", "*,5 * * * *",
    "* * * * 5L/2", "#3 * * * *", "W * * * *",
    "@every 5m", "* * * * 5#6",
])
def test_invalid_expressions_are_refused(expression):
    with pytest.raises(CronError):
        parse(expression)


def test_errors_say_what_is_wrong():
    with pytest.raises(CronError) as excinfo:
        parse("0 9 * *")
    assert "5 fields" in str(excinfo.value)

    with pytest.raises(CronError) as excinfo:
        parse("0 25 * * *")
    assert "outside 0-23" in str(excinfo.value)


def test_macros_expand_to_their_five_fields():
    assert parse("@daily").expression == "0 0 * * *"
    assert parse("@hourly").expression == "0 * * * *"
    assert parse("@weekly").expression == "0 0 * * 0"


def test_names_are_case_insensitive_and_ranges_work():
    assert parse("0 9 * * mon-fri").matches(datetime(2024, 1, 1, 9, 0))   # Monday
    assert not parse("0 9 * * mon-fri").matches(datetime(2024, 1, 6, 9, 0))  # Saturday
    assert parse("0 0 1 jan *").matches(datetime(2024, 1, 1, 0, 0))
    assert not parse("0 0 1 jan *").matches(datetime(2024, 2, 1, 0, 0))


# ------------------------------------------------------------- matching
def test_a_simple_expression_matches_only_its_minute():
    expression = parse("30 6 * * *")
    assert expression.matches(datetime(2024, 3, 4, 6, 30))
    assert not expression.matches(datetime(2024, 3, 4, 6, 31))
    assert not expression.matches(datetime(2024, 3, 4, 7, 30))


def test_steps_and_lists():
    expression = parse("0,30 9-17 * * *")
    assert expression.matches(datetime(2024, 3, 4, 9, 0))
    assert expression.matches(datetime(2024, 3, 4, 17, 30))
    assert not expression.matches(datetime(2024, 3, 4, 8, 0))
    assert not expression.matches(datetime(2024, 3, 4, 9, 15))


def test_day_of_month_and_weekday_are_ored():
    # Classic cron: both restricted means either one is enough.
    expression = parse("0 0 13 * fri")
    assert expression.matches(datetime(2024, 1, 5, 0, 0))    # a Friday
    assert expression.matches(datetime(2024, 1, 13, 0, 0))   # the 13th, a Saturday
    assert not expression.matches(datetime(2024, 1, 11, 0, 0))   # Thursday, not the 13th


def test_weekday_only_and_day_only_stay_separate():
    assert parse("0 0 * * fri").matches(datetime(2024, 1, 5, 0, 0))
    assert not parse("0 0 * * fri").matches(datetime(2024, 1, 13, 0, 0))
    assert parse("0 0 13 * *").matches(datetime(2024, 1, 13, 0, 0))
    assert not parse("0 0 13 * *").matches(datetime(2024, 1, 5, 0, 0))


def test_sunday_can_be_written_as_seven():
    assert parse("0 0 * * 7").matches(datetime(2024, 1, 7, 0, 0))
    assert parse("0 0 * * 0").matches(datetime(2024, 1, 7, 0, 0))


def test_last_day_of_month():
    expression = parse("0 0 L * *")
    assert expression.matches(datetime(2024, 1, 31, 0, 0))
    assert not expression.matches(datetime(2024, 1, 30, 0, 0))
    assert expression.matches(datetime(2024, 2, 29, 0, 0))   # leap year
    assert expression.matches(datetime(2023, 2, 28, 0, 0))


def test_last_weekday_of_month():
    expression = parse("0 0 * * 5L")
    assert expression.matches(datetime(2024, 1, 26, 0, 0))   # last Friday in Jan 2024
    assert not expression.matches(datetime(2024, 1, 19, 0, 0))
    assert expression.matches(datetime(2024, 3, 29, 0, 0))   # last Friday in Mar 2024


def test_nearest_weekday_never_lands_on_a_weekend():
    expression = parse("0 0 15W * *")
    # 2024-06-15 is a Saturday, so the nearest weekday is Friday the 14th.
    assert expression.matches(datetime(2024, 6, 14, 0, 0))
    assert not expression.matches(datetime(2024, 6, 15, 0, 0))
    # 2024-09-15 is a Sunday, so it moves forward to Monday the 16th.
    assert expression.matches(datetime(2024, 9, 16, 0, 0))
    assert not expression.matches(datetime(2024, 9, 15, 0, 0))
    # A normal weekday is left alone.
    assert expression.matches(datetime(2024, 5, 15, 0, 0))


def test_nth_weekday_of_month():
    expression = parse("0 0 * * 5#3")
    assert expression.matches(datetime(2024, 1, 19, 0, 0))   # third Friday
    assert not expression.matches(datetime(2024, 1, 12, 0, 0))
    assert not expression.matches(datetime(2024, 1, 26, 0, 0))


def test_fifth_weekday_is_skipped_in_months_without_one():
    expression = parse("0 0 * * 1#5")
    assert expression.matches(datetime(2024, 1, 29, 0, 0))   # 5th Monday in Jan 2024
    assert not expression.matches(datetime(2024, 2, 26, 0, 0))  # Feb 2024 has four


def test_epoch_periodicity():
    """%n is Jarvis's extension: every n units since 1970-01-01."""
    expression = parse("%2 * * * *")
    base = datetime(1970, 1, 1, 0, 0)
    assert expression.matches(base)
    assert not expression.matches(base + timedelta(minutes=1))
    assert expression.matches(base + timedelta(minutes=2))


# --------------------------------------------------------------- searching
def test_next_after_is_strictly_later():
    expression = parse("0 * * * *")
    assert expression.next_after(datetime(2024, 5, 1, 7, 0)) == datetime(2024, 5, 1, 8, 0)
    assert expression.next_after(datetime(2024, 5, 1, 7, 59)) == datetime(2024, 5, 1, 8, 0)


def test_next_after_crosses_month_and_year_boundaries():
    assert parse("0 0 1 * *").next_after(datetime(2024, 1, 31, 23, 59)) == \
        datetime(2024, 2, 1, 0, 0)
    assert parse("0 0 1 1 *").next_after(datetime(2024, 6, 1, 0, 0)) == \
        datetime(2025, 1, 1, 0, 0)


def test_next_after_handles_a_leap_day():
    assert parse("0 0 29 2 *").next_after(datetime(2023, 3, 1, 0, 0)) == \
        datetime(2024, 2, 29, 0, 0)
    assert parse("0 0 29 2 *").next_after(datetime(2024, 3, 1, 0, 0)) == \
        datetime(2028, 2, 29, 0, 0)


def test_an_expression_that_can_never_fire_is_reported():
    # February never has 30 days.
    with pytest.raises(CronError):
        parse("0 0 30 2 *").next_after(datetime(2024, 1, 1))


def test_next_after_is_fast_enough_for_a_form_preview():
    start = time.perf_counter()
    for _ in range(20):
        parse("0 9 * * mon-fri").next_after(datetime(2024, 1, 1, 0, 0))
        parse("0 0 L * *").next_after(datetime(2024, 1, 1, 0, 0))
    elapsed = time.perf_counter() - start
    assert elapsed < 2.0, f"40 next-run calculations took {elapsed:.2f}s"


def test_upcoming_returns_a_series():
    times = parse("0 0 * * *").upcoming(datetime(2024, 1, 1, 12, 0), count=3)
    assert [t.day for t in times] == [2, 3, 4]


# ------------------------------------------- 10,000 random minutes vs brute force
def _reference_expand(text: str, low: int, high: int) -> set[int]:
    values: set[int] = set()
    for part in text.split(","):
        step = 1
        body = part
        if "/" in part:
            body, _, step_text = part.partition("/")
            step = int(step_text)
        if body == "*":
            start, end = low, high
        elif "-" in body:
            start, end = (int(x) for x in body.split("-"))
        else:
            start = end = int(body)
        values.update(range(start, end + 1, step))
    return values


def _reference_matches(expression: str, when: datetime) -> bool:
    """A separate, deliberately dull implementation of the five standard fields."""
    minutes, hours, dom, months, dow = expression.split()
    if when.minute not in _reference_expand(minutes, 0, 59):
        return False
    if when.hour not in _reference_expand(hours, 0, 23):
        return False
    if when.month not in _reference_expand(months, 1, 12):
        return False
    dom_values = _reference_expand(dom, 1, 31)
    dow_values = {v % 7 for v in _reference_expand(dow, 0, 7)}
    dom_ok = when.day in dom_values
    dow_ok = (when.weekday() + 1) % 7 in dow_values
    dom_star = dom.strip() == "*"
    dow_star = dow.strip() == "*"
    if dom_star and dow_star:
        return True
    if dom_star:
        return dow_ok
    if dow_star:
        return dom_ok
    return dom_ok or dow_ok


def _random_field(rng: random.Random, low: int, high: int) -> str:
    kind = rng.choice(("star", "value", "range", "step", "range_step", "list"))
    if kind == "star":
        return "*"
    if kind == "value":
        return str(rng.randint(low, high))
    if kind == "range":
        start = rng.randint(low, high - 1)
        return f"{start}-{rng.randint(start + 1, high)}"
    if kind == "step":
        return f"*/{rng.randint(2, max(2, (high - low) // 2))}"
    if kind == "range_step":
        start = rng.randint(low, high - 2)
        end = rng.randint(start + 1, high)
        return f"{start}-{end}/{rng.randint(2, 4)}"
    first = rng.randint(low, high)
    second = rng.randint(low, high)
    return f"{first},{second}"


def test_ten_thousand_random_minutes_agree_with_brute_force():
    rng = random.Random(20240601)
    checked = 0
    for _ in range(40):
        expression = " ".join((
            _random_field(rng, 0, 59), _random_field(rng, 0, 23),
            _random_field(rng, 1, 31), _random_field(rng, 1, 12),
            _random_field(rng, 0, 7),
        ))
        parsed = parse(expression)
        for _ in range(250):
            when = datetime(2024, 1, 1) + timedelta(
                days=rng.randint(0, 1500), minutes=rng.randint(0, 1439))
            assert parsed.matches(when) == _reference_matches(expression, when), (
                f"{expression!r} disagreed at {when}")
            checked += 1
    assert checked == 10_000


def test_next_after_agrees_with_a_minute_by_minute_scan():
    rng = random.Random(99)
    for _ in range(25):
        expression = " ".join((
            _random_field(rng, 0, 59), _random_field(rng, 0, 23),
            _random_field(rng, 1, 28), _random_field(rng, 1, 12), "*",
        ))
        parsed = parse(expression)
        start = datetime(2024, 1, 1) + timedelta(minutes=rng.randint(0, 100_000))
        expected = None
        cursor = start.replace(second=0) + timedelta(minutes=1)
        for _ in range(60 * 24 * 40):          # 40 days of minutes
            if _reference_matches(expression, cursor):
                expected = cursor
                break
            cursor += timedelta(minutes=1)
        if expected is None:
            continue
        assert parsed.next_after(start) == expected, expression


# ------------------------------------------------------------- the scheduler
def _scheduler(tmp_path, calls: list[str]):
    return JobScheduler(str(tmp_path / "jobs.db"),
                        executor=lambda job, progress: calls.append(job["goal"]) or {},
                        autostart=False)


def test_a_calendar_job_starts_at_the_next_matching_minute(tmp_path):
    sched = _scheduler(tmp_path, [])
    try:
        job = sched.create("Daily", goal="brief", schedule="cron", cron="0 9 * * *")
        expected = CronExpression("0 9 * * *").next_after_ts(time.time())
        assert job["cron"] == "0 9 * * *"
        assert abs(job["next_run_at"] - expected) < 2
        assert job["next_run_at"] > time.time()
    finally:
        sched.close()


def test_an_invalid_expression_is_refused_before_it_is_stored(tmp_path):
    sched = _scheduler(tmp_path, [])
    try:
        with pytest.raises(JobError):
            sched.create("Bad", goal="x", schedule="cron", cron="0 25 * * *")
        with pytest.raises(JobError):
            sched.create("Missing", goal="x", schedule="cron")
        assert sched.list() == []
    finally:
        sched.close()


def test_a_calendar_job_fires_once_and_schedules_from_the_calendar(tmp_path):
    calls: list[str] = []
    sched = _scheduler(tmp_path, calls)
    try:
        # Every minute, and already due, so the first tick runs it.
        job = sched.create("Tick", goal="minute", schedule="cron", cron="* * * * *",
                           next_run_at=time.time() - 1)
        sched._tick_once()
        deadline = time.time() + 6
        while time.time() < deadline and sched.get(job["id"])["status"] != "scheduled":
            time.sleep(0.05)
        stored = sched.get(job["id"])
        assert calls == ["minute"]
        assert stored["status"] == "scheduled"
        # Next run is the next whole minute, not "now + something".
        assert stored["next_run_at"] % 60 == 0
        assert stored["next_run_at"] > time.time() - 1
    finally:
        sched.close()


def test_repeated_ticks_in_the_same_minute_do_not_run_a_job_twice(tmp_path):
    calls: list[str] = []
    sched = _scheduler(tmp_path, calls)
    try:
        job = sched.create("Guard", goal="once-per-minute", schedule="cron",
                           cron="* * * * *", next_run_at=time.time() - 1)
        for _ in range(5):
            sched._tick_once()
            time.sleep(0.05)
        time.sleep(0.4)
        assert calls == ["once-per-minute"]
        assert sched.get(job["id"])["run_count"] == 1
        # A second tick inside the same minute must not claim it again.
        sched.update(job["id"], {"status": "scheduled", "next_run_at": time.time() - 1})
        sched._tick_once()
        time.sleep(0.4)
        assert calls == ["once-per-minute"]
    finally:
        sched.close()


def test_a_calendar_schedule_survives_a_restart(tmp_path):
    first = _scheduler(tmp_path, [])
    job = first.create("Persist", goal="stay", schedule="cron", cron="0 3 * * *")
    first.close()

    second = _scheduler(tmp_path, [])
    try:
        stored = second.get(job["id"])
        assert stored["schedule"] == "cron"
        assert stored["cron"] == "0 3 * * *"
        assert stored["next_run_at"] == job["next_run_at"]
    finally:
        second.close()


def test_switching_to_a_calendar_reanchors_the_next_run(tmp_path):
    sched = _scheduler(tmp_path, [])
    try:
        job = sched.create("Switch", goal="x", schedule="interval", interval_sec=600)
        updated = sched.update(job["id"], {"schedule": "cron", "cron": "0 9 * * *"})
        assert updated["cron"] == "0 9 * * *"
        assert updated["next_run_at"] > time.time()
        assert datetime.fromtimestamp(updated["next_run_at"]).hour == 9
        # And back again clears the expression.
        back = sched.update(job["id"], {"schedule": "interval", "interval_sec": 300})
        assert back["cron"] is None
    finally:
        sched.close()


def test_interval_jobs_are_untouched_by_the_cron_column(tmp_path):
    calls: list[str] = []
    sched = _scheduler(tmp_path, calls)
    try:
        job = sched.create("Every", goal="tick", schedule="interval",
                           interval_sec=60, next_run_at=time.time() - 1)
        assert job["cron"] is None
        sched._tick_once()
        deadline = time.time() + 6
        while time.time() < deadline and sched.get(job["id"])["status"] != "scheduled":
            time.sleep(0.05)
        stored = sched.get(job["id"])
        assert stored["status"] == "scheduled"
        # Interval schedules count from the finish time, not from the calendar.
        assert stored["next_run_at"] % 60 != 0 or stored["next_run_at"] > time.time()
        assert stored["next_run_at"] > time.time() + 50
    finally:
        sched.close()


def test_next_after_helper_accepts_a_timestamp():
    start = datetime(2024, 1, 1, 10, 30).timestamp()
    assert next_after("0 11 * * *", start) == datetime(2024, 1, 1, 11, 0).timestamp()


# ------------------------------------------------------------------- the API
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


def test_api_previews_a_valid_expression(client):
    response = client.get("/api/cron/preview", params={"expression": "0 9 * * mon-fri"})
    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["expression"] == "0 9 * * mon-fri"
    assert len(body["next"]) == 3
    assert len(body["next_local"]) == 3
    assert all(earlier < later for earlier, later in zip(body["next"], body["next"][1:]))


def test_api_preview_explains_an_invalid_expression(client):
    body = client.get("/api/cron/preview",
                      params={"expression": "0 99 * * *"}).json()
    assert body["valid"] is False
    assert "outside 0-23" in body["error"]
    assert body["next"] == []


def test_api_creates_a_calendar_job_and_rejects_a_bad_one(client):
    created = client.post("/api/jobs", json={
        "name": "Weekday brief", "goal": "Summarise overnight issues",
        "kind": "prompt", "schedule": "cron", "cron": "0 8 * * mon-fri"})
    assert created.status_code == 200, created.text
    job = created.json()["job"]
    assert job["schedule"] == "cron"
    assert job["cron"] == "0 8 * * mon-fri"
    assert job["next_run_at"] > time.time()

    rejected = client.post("/api/jobs", json={
        "name": "Bad", "goal": "x", "schedule": "cron", "cron": "0 8 * * funday"})
    assert rejected.status_code == 400
    assert "funday" in rejected.json()["detail"] or "unrecognised" in rejected.json()["detail"]

    missing = client.post("/api/jobs", json={
        "name": "No expression", "goal": "x", "schedule": "cron"})
    assert missing.status_code == 400

    assert len(client.get("/api/jobs").json()["jobs"]) == 1
