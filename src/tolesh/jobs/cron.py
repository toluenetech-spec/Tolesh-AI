"""Cron expression matching for calendar-based job schedules.

The field parser and the ``L`` / ``W`` / ``#`` / ``%`` rules are adapted from
thevickypedia/Jarvis (MIT licence), ``jarvis/modules/crontab/expression.py``.
That file also splits a trailing command off the expression and hands it to a
shell; none of that is carried over — this module only answers "does this
minute match", and it never sees a command.

What is added on top of the original: ``next_after``, which the scheduler needs
because it wakes on a timer rather than polling once a minute, and a day-first
search so a rare expression does not cost a minute-by-minute scan.

Supported syntax, per field:

    *            every value
    5            one value
    1-5          a range
    */10         a step over the whole range
    1-5/2        a step over a range
    5/10         a step from 5 to the end of the range
    jan, mon     3-letter names (months 1-12, weekdays sun-sat = 0-6)
    ?            synonym for * (Quartz habit)
    7            Sunday, in the weekday field
    L            last day of the month (day-of-month field)
    5L           last Friday of the month (weekday field)
    15W          nearest weekday to the 15th (day-of-month field)
    5#3          the third Friday of the month (weekday field)
    %15          every 15th minute/hour/day/month since the epoch

``%n`` is Jarvis's own extension, not part of Vixie cron (where ``%`` separates
the command from stdin). It is kept because the audit asked for it, and because
it is the only way to express "every other day regardless of date". Its
ordinal is counted from 1970-01-01 in the field's own unit, so it depends on the
epoch — an expression using it is not portable to another implementation.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, datetime, time as dtime, timedelta

__all__ = ["CronError", "CronExpression", "parse", "next_after"]

_MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
           "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_WEEKDAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}

_MONTH_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(r"\b(" + "|".join(_WEEKDAYS) + r")\b", re.IGNORECASE)

MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

# (low, high) per field, in the order minute, hour, day-of-month, month, weekday
_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))

# A search that needs more than this many days has no practical next run.
_HORIZON_DAYS = 366 * 5


class CronError(ValueError):
    """The expression cannot be parsed, or can never fire."""


class _Field:
    """One parsed field: the plain values, plus the atoms needing calendar math."""

    __slots__ = ("values", "atoms", "raw", "lo", "hi")

    def __init__(self, raw: str, lo: int, hi: int):
        self.raw = raw
        self.lo = lo
        self.hi = hi
        self.values: set[int] = set()
        self.atoms: list[str] = []

    @property
    def unrestricted(self) -> bool:
        return self.raw in ("*", "?")

    def sorted_values(self) -> list[int]:
        return sorted(self.values) if self.values else list(range(self.lo, self.hi + 1))

    def ok(self, value: int, ordinal: int | None = None) -> bool:
        if value in self.values:
            return True
        for atom in self.atoms:
            if atom[0] == "%":
                step = int(atom[1:])
                if ordinal is not None and step and ordinal % step == 0:
                    return True
        return False


def _parse_atom(atom: str, lo: int, hi: int) -> set[int]:
    """Expand one plain atom (``*``, ``n``, ``a-b``, ``*/s``, ``a-b/s``, ``a/s``)."""
    atom = atom.strip()
    if not atom:
        raise CronError("empty field")
    step = 1
    body = atom
    if "/" in atom:
        body, _, step_text = atom.partition("/")
        if not step_text.isdigit() or int(step_text) < 1:
            raise CronError(f"invalid step in {atom!r}")
        step = int(step_text)
        if step > hi:
            raise CronError(f"step {step} is larger than the field maximum {hi}")

    if body == "*":
        start, end = lo, hi
    elif "-" in body:
        first, _, last = body.partition("-")
        if not first.isdigit() or not last.isdigit():
            raise CronError(f"invalid range in {atom!r}")
        start, end = int(first), int(last)
        if start < lo or end > hi:
            raise CronError(f"{atom!r} is outside {lo}-{hi}")
        if start > end:
            raise CronError(f"{atom!r} runs backwards")
    elif body.isdigit():
        start = int(body)
        if start < lo or start > hi:
            raise CronError(f"{atom!r} is outside {lo}-{hi}")
        # "5/10" has no standard meaning; read it as "from 5 to the end, every 10".
        end = hi if step > 1 else start
    else:
        raise CronError(f"unrecognised field value {atom!r}")

    return set(range(start, end + 1, step))


def _parse_field(text: str, lo: int, hi: int, *, weekday: bool = False) -> _Field:
    raw = text.strip()
    field = _Field(raw, lo, hi)
    parts = [p for p in raw.split(",") if p != ""]
    if not parts:
        raise CronError("empty field")
    if len(parts) > 1 and "*" in parts:
        raise CronError('"*" must be alone in a field')

    for atom in parts:
        upper = atom.upper()
        if "%" in atom:
            if not atom.startswith("%") or not atom[1:].isdigit() or int(atom[1:]) < 1:
                raise CronError(f"invalid % period in {atom!r}")
            field.atoms.append(atom)
            continue
        if "#" in atom:
            if not weekday:
                raise CronError(f"# is only valid in the weekday field, not {atom!r}")
            day_text, _, nth_text = atom.partition("#")
            if not day_text.isdigit() or not nth_text.isdigit():
                raise CronError(f"invalid weekday ordinal {atom!r}")
            day, nth = int(day_text), int(nth_text)
            if not 0 <= day <= 7 or not 1 <= nth <= 5:
                raise CronError(f"{atom!r} must be weekday 0-7 # 1-5")
            field.atoms.append(f"{day % 7}#{nth}")
            continue
        if upper.endswith("W"):
            if weekday:
                raise CronError(f"W is only valid in the day-of-month field, not {atom!r}")
            head = atom[:-1]
            if not head.isdigit() or not 1 <= int(head) <= 31:
                raise CronError(f"invalid nearest-weekday {atom!r}")
            field.atoms.append(f"{int(head)}W")
            continue
        if upper.endswith("L"):
            head = atom[:-1]
            if head == "":
                if weekday:
                    raise CronError("L alone means the last day of the month; "
                                    "use 5L for the last Friday")
                field.atoms.append("L")
                continue
            if not weekday:
                raise CronError(f"{atom!r}: a weekday before L only makes sense "
                                "in the weekday field")
            if not head.isdigit() or not 0 <= int(head) <= 7:
                raise CronError(f"invalid last-weekday {atom!r}")
            field.atoms.append(f"{int(head) % 7}L")
            continue
        field.values.update(_parse_atom(atom, lo, hi))

    if weekday:
        # 7 and 0 are both Sunday.
        if 7 in field.values:
            field.values.discard(7)
            field.values.add(0)
    return field


def _nearest_weekday(year: int, month: int, day: int) -> int:
    """Day-of-month of the weekday nearest ``day``, never leaving the month."""
    last = calendar.monthrange(year, month)[1]
    target = min(day, last)
    weekday = date(year, month, target).weekday()  # Monday == 0
    if weekday == 5:                                # Saturday -> the Friday before
        target = target - 1 if target > 1 else target + 2
    elif weekday == 6:                              # Sunday -> the Monday after
        target = target + 1 if target < last else target - 2
    return target


class CronExpression:
    """A validated five-field cron expression."""

    def __init__(self, expression: str):
        if not isinstance(expression, str) or not expression.strip():
            raise CronError("A cron expression cannot be empty.")
        text = expression.strip()
        lowered = text.lower()
        for macro, expansion in MACROS.items():
            if lowered == macro or lowered.startswith(macro + " "):
                text = expansion
                break
        fields = text.split()
        if len(fields) != 5:
            raise CronError(
                f"Expected 5 fields (minute hour day month weekday), got {len(fields)}.")
        minutes, hours, dom, months, dow = fields
        months = _MONTH_RE.sub(lambda m: str(_MONTHS[m.group(1).lower()]), months)
        dow = _WEEKDAY_RE.sub(lambda m: str(_WEEKDAYS[m.group(1).lower()]), dow)

        self.minute = _parse_field(minutes, *_BOUNDS[0])
        self.hour = _parse_field(hours, *_BOUNDS[1])
        self.dom = _parse_field(dom.replace("?", "*"), *_BOUNDS[2])
        self.month = _parse_field(months, *_BOUNDS[3])
        self.dow = _parse_field(dow.replace("?", "*"), *_BOUNDS[4], weekday=True)
        self.expression = " ".join(f.strip() for f in fields)

    # ------------------------------------------------------------- matching
    def __str__(self) -> str:
        return self.expression

    def __repr__(self) -> str:
        return f"CronExpression({self.expression!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, CronExpression) and other.expression == self.expression

    def __hash__(self) -> int:
        return hash(self.expression)

    @staticmethod
    def _ordinal(when: datetime, unit: str) -> int:
        """Whole ``unit``s between the epoch and ``when``."""
        delta = when - datetime(1970, 1, 1)
        if unit == "minute":
            return int(delta.total_seconds() // 60)
        if unit == "hour":
            return int(delta.total_seconds() // 3600)
        if unit == "day":
            return delta.days
        if unit == "month":
            return (when.year - 1970) * 12 + when.month - 1
        raise AssertionError(unit)

    def _month_ok(self, when: datetime) -> bool:
        return self.month.ok(when.month, self._ordinal(when, "month"))

    def _dom_ok(self, day: date) -> bool:
        if self.dom.ok(day.day, self._ordinal(datetime.combine(day, dtime()), "day")):
            return True
        last = calendar.monthrange(day.year, day.month)[1]
        for atom in self.dom.atoms:
            if atom == "L" and day.day == last:
                return True
            if atom.endswith("W"):
                if day.day == _nearest_weekday(day.year, day.month, int(atom[:-1])):
                    return True
        return False

    def _dow_ok(self, day: date) -> bool:
        weekday = (day.weekday() + 1) % 7          # Sunday == 0, as in cron
        if self.dow.ok(weekday, self._ordinal(datetime.combine(day, dtime()), "day")):
            return True
        last = calendar.monthrange(day.year, day.month)[1]
        first_weekday = (date(day.year, day.month, 1).weekday() + 1) % 7
        for atom in self.dow.atoms:
            if atom.endswith("L"):
                # Day-of-month of the last occurrence of this weekday.
                target = ((int(atom[:-1]) - first_weekday) % 7) + 29
                if target > last:
                    target -= 7
                if day.day == target:
                    return True
            elif "#" in atom:
                wanted, nth = (int(n) for n in atom.split("#"))
                target = 1 + (wanted - first_weekday) % 7 + 7 * (nth - 1)
                if target <= last and day.day == target:
                    return True
        return False

    def _day_ok(self, day: date) -> bool:
        dom_restricted = not self.dom.unrestricted
        dow_restricted = not self.dow.unrestricted
        if dom_restricted and dow_restricted:
            # Classic cron: either field matching is enough.
            return self._dom_ok(day) or self._dow_ok(day)
        if dom_restricted:
            return self._dom_ok(day)
        if dow_restricted:
            return self._dow_ok(day)
        return True

    def matches(self, when: datetime) -> bool:
        """True when ``when`` falls on a minute this expression fires."""
        return (self.minute.ok(when.minute, self._ordinal(when, "minute"))
                and self.hour.ok(when.hour, self._ordinal(when, "hour"))
                and self._month_ok(when)
                and self._day_ok(when.date()))

    # -------------------------------------------------------------- forward
    def next_after(self, when: datetime) -> datetime:
        """The first firing minute strictly after ``when``.

        Searches day by day and only walks hours and minutes on a day that can
        match, so ``0 0 29 2 *`` costs a few hundred day checks rather than a
        million minute checks.
        """
        start = when.replace(second=0, microsecond=0) + timedelta(minutes=1)
        hours = self.hour.sorted_values()
        minutes = self.minute.sorted_values()
        day = start.date()
        for _ in range(_HORIZON_DAYS):
            if self._month_ok(datetime.combine(day, dtime())) and self._day_ok(day):
                for hour in hours:
                    for minute in minutes:
                        candidate = datetime.combine(day, dtime(hour, minute))
                        if candidate < start:
                            continue
                        if self.matches(candidate):
                            return candidate
            day += timedelta(days=1)
        raise CronError(f"{self.expression!r} does not fire within "
                        f"{_HORIZON_DAYS} days of {when.isoformat()}")

    def next_after_ts(self, timestamp: float) -> float:
        """``next_after`` for an epoch timestamp, in local time."""
        return self.next_after(datetime.fromtimestamp(timestamp)).timestamp()

    def upcoming(self, when: datetime, count: int = 3) -> list[datetime]:
        """The next ``count`` firing times — for the UI's preview."""
        found: list[datetime] = []
        cursor = when
        for _ in range(max(1, min(count, 10))):
            cursor = self.next_after(cursor)
            found.append(cursor)
        return found


def parse(expression: str) -> CronExpression:
    """Validate ``expression`` and return the matcher."""
    return CronExpression(expression)


def next_after(expression: str, timestamp: float) -> float:
    """Next firing time for ``expression`` after ``timestamp``."""
    return CronExpression(expression).next_after_ts(timestamp)
