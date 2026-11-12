"""Five-field cron expressions, evaluated in a time zone.

The scheduler stores instants in UTC and reads a job's expression in the job's own
zone. Two rules decide what happens around daylight-saving changes, and both follow
from one principle: **a schedule fires at most once per wall-clock time.**

* A wall time that does not exist because the clocks jumped forward fires once, at
  the instant of the jump (``30 2 * * *`` in Europe/Berlin fires at 03:00 CEST on the
  last Sunday of March).
* A wall time that occurs twice because the clocks went back fires only the first
  time.

Syntax is classic Vixie cron: ``minute hour day-of-month month day-of-week`` with
``*``, lists, ranges, steps (``*/15``, ``1-10/2``, ``5/10``), month and weekday names
(``JAN``-``DEC``, ``SUN``-``SAT``, case-insensitive), ``7`` as a second Sunday, and the
macros ``@yearly @annually @monthly @weekly @daily @midnight @hourly``. When both
day fields are restricted a day matches if *either* matches; a field counts as
unrestricted when it starts with ``*``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone as dt_timezone, tzinfo
from typing import FrozenSet, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = ["CronError", "CronSchedule", "describe", "resolve_timezone"]

#: How far ahead a schedule is searched. Long enough for every expression that can
#: match at all (29 February on a given weekday repeats within 28 years only in
#: theory; every real schedule fires within a few years).
_HORIZON_YEARS = 10

_MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}

_MONTH_NAMES = {
    name: number
    for number, name in enumerate(
        ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"),
        start=1,
    )
}
_WEEKDAY_NAMES = {
    name: number for number, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))
}

_FIELD_SPECS = (
    # (label, low, high, names)
    ("minute", 0, 59, {}),
    ("hour", 0, 23, {}),
    ("day of month", 1, 31, {}),
    ("month", 1, 12, _MONTH_NAMES),
    ("day of week", 0, 7, _WEEKDAY_NAMES),
)


class CronError(ValueError):
    """A cron expression or time zone that cannot be used, with a reason a person can act on."""


def resolve_timezone(name: str) -> tzinfo:
    """The ``ZoneInfo`` for an IANA zone name, or :class:`CronError` saying why not."""
    if not name or not str(name).strip():
        raise CronError("A time zone is required, for example 'Europe/Berlin' or 'UTC'.")
    try:
        return ZoneInfo(str(name).strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise CronError(
            f"'{name}' is not a known time zone. Use an IANA name such as 'Europe/Berlin' or 'UTC'."
        ) from exc


def _parse_value(token: str, label: str, low: int, high: int, names: dict) -> int:
    upper = token.upper()
    if upper in names:
        return names[upper]
    if not token.isdigit():
        raise CronError(f"'{token}' is not a valid {label}.")
    value = int(token)
    if not low <= value <= high:
        raise CronError(f"{label.capitalize()} {value} is out of range ({low}-{high}).")
    return value


def _parse_field(text: str, label: str, low: int, high: int, names: dict) -> FrozenSet[int]:
    values = set()
    for part in text.split(","):
        if not part:
            raise CronError(f"The {label} field has an empty list entry.")
        step = 1
        if "/" in part:
            base, _, step_text = part.partition("/")
            if not step_text.isdigit() or int(step_text) == 0:
                raise CronError(f"'{step_text}' is not a valid step in the {label} field.")
            step = int(step_text)
        else:
            base = part
        if base == "*":
            start, end = low, high
        elif "-" in base:
            first, _, last = base.partition("-")
            start = _parse_value(first, label, low, high, names)
            end = _parse_value(last, label, low, high, names)
            if start > end:
                raise CronError(f"The range '{base}' in the {label} field runs backwards.")
        else:
            start = _parse_value(base, label, low, high, names)
            # ``5/10`` means "from 5 to the end, every 10"; a bare value is just itself.
            end = high if "/" in part else start
        values.update(range(start, end + 1, step))
    return frozenset(values)


@dataclass(frozen=True)
class CronSchedule:
    """A parsed cron expression. Build it with :meth:`parse`."""

    expression: str
    minutes: FrozenSet[int]
    hours: FrozenSet[int]
    days_of_month: FrozenSet[int]
    months: FrozenSet[int]
    days_of_week: FrozenSet[int]  # 0 = Sunday … 6 = Saturday
    day_of_month_restricted: bool
    day_of_week_restricted: bool

    @classmethod
    def parse(cls, expression: str) -> "CronSchedule":
        """Parse ``expression`` or raise :class:`CronError` naming what is wrong."""
        text = " ".join(str(expression or "").split())
        if not text:
            raise CronError("A cron expression is required, for example '0 6 * * 1-5'.")
        if text.startswith("@"):
            if text.lower() == "@reboot":
                raise CronError("'@reboot' is not supported: a schedule needs a time.")
            if text.lower() not in _MACROS:
                raise CronError(
                    f"'{text}' is not a known macro. Use one of: {', '.join(sorted(_MACROS))}."
                )
            fields = _MACROS[text.lower()].split(" ")
        else:
            fields = text.split(" ")
        if len(fields) == 6:
            raise CronError(
                "Six fields were given. Schedules run at minute precision, so use five: "
                "minute hour day-of-month month day-of-week."
            )
        if len(fields) != 5:
            raise CronError(
                f"A cron expression has five fields (minute hour day-of-month month day-of-week); "
                f"got {len(fields)}."
            )
        parsed = [
            _parse_field(field, label, low, high, names)
            for field, (label, low, high, names) in zip(fields, _FIELD_SPECS)
        ]
        days_of_week = frozenset(0 if day == 7 else day for day in parsed[4])
        schedule = cls(
            expression=text,
            minutes=parsed[0],
            hours=parsed[1],
            days_of_month=parsed[2],
            months=parsed[3],
            days_of_week=days_of_week,
            day_of_month_restricted=not fields[2].startswith("*"),
            day_of_week_restricted=not fields[4].startswith("*"),
        )
        if schedule._next_wall_time(datetime(2000, 1, 1)) is None:
            raise CronError(f"'{text}' never fires: no date matches it.")
        return schedule

    # ── matching ──────────────────────────────────────────────────────

    def _day_matches(self, moment: datetime) -> bool:
        dom = moment.day in self.days_of_month
        dow = (moment.isoweekday() % 7) in self.days_of_week
        if self.day_of_month_restricted and self.day_of_week_restricted:
            return dom or dow
        return dom and dow

    def _next_wall_time(self, start: datetime) -> Optional[datetime]:
        """The first naive wall time ``>= start`` (whole minutes) that matches, or ``None``."""
        moment = start.replace(second=0, microsecond=0)
        if moment < start:
            moment += timedelta(minutes=1)
        limit_year = start.year + _HORIZON_YEARS
        minutes = sorted(self.minutes)
        while moment.year <= limit_year:
            if moment.month not in self.months:
                year = moment.year + (moment.month == 12)
                month = moment.month % 12 + 1
                moment = datetime(year, month, 1)
                continue
            if not self._day_matches(moment):
                moment = datetime(moment.year, moment.month, moment.day) + timedelta(days=1)
                continue
            if moment.hour not in self.hours:
                moment = moment.replace(minute=0) + timedelta(hours=1)
                continue
            later = [minute for minute in minutes if minute >= moment.minute]
            if not later:
                moment = moment.replace(minute=0) + timedelta(hours=1)
                continue
            return moment.replace(minute=later[0])
        return None

    # ── instants ──────────────────────────────────────────────────────

    def next_after(self, after: datetime, tz: tzinfo) -> Optional[datetime]:
        """The first instant strictly after ``after`` at which the schedule fires, in UTC.

        ``after`` must be timezone-aware. Returns ``None`` only when nothing matches
        within the search horizon, which :meth:`parse` already refuses for real input.
        """
        if after.tzinfo is None:
            raise ValueError("next_after() needs a timezone-aware datetime.")
        wall = after.astimezone(tz).replace(tzinfo=None, second=0, microsecond=0) + timedelta(minutes=1)
        while True:
            candidate = self._next_wall_time(wall)
            if candidate is None:
                return None
            instant = _wall_to_instant(candidate, tz)
            if instant > after:
                return instant
            wall = candidate + timedelta(minutes=1)


def _wall_to_instant(wall: datetime, tz: tzinfo) -> datetime:
    """The UTC instant a naive wall time denotes in ``tz``, under the DST rules above."""
    first = wall.replace(tzinfo=tz, fold=0)
    # A wall time that does not exist round-trips to a different wall time.
    if first.astimezone(dt_timezone.utc).astimezone(tz).replace(tzinfo=None) != wall:
        return _gap_end(wall, tz)
    # An ambiguous wall time: fold=0 is its first occurrence, which is the one that fires.
    return first.astimezone(dt_timezone.utc)


def _gap_end(wall: datetime, tz: tzinfo) -> datetime:
    """The instant the clocks jumped over ``wall`` — the first existing instant after it."""
    low = wall.replace(tzinfo=tz, fold=1).astimezone(dt_timezone.utc)
    high = wall.replace(tzinfo=tz, fold=0).astimezone(dt_timezone.utc)
    low, high = min(low, high), max(low, high)
    probe = low.replace(second=0, microsecond=0)
    while probe <= high:
        local = probe.astimezone(tz).replace(tzinfo=None)
        if local > wall:
            return probe
        probe += timedelta(minutes=1)
    return high


def describe(expression: str) -> str:
    """A readable sentence for ``expression`` when ``cron-descriptor`` is installed, else itself."""
    try:
        from cron_descriptor import get_description
    except ImportError:
        return expression
    text = " ".join(str(expression or "").split())
    try:
        return get_description(_MACROS.get(text.lower(), text))
    except Exception:  # noqa: BLE001 - a description is a nicety, never a failure
        return expression
