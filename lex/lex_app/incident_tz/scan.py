"""Read-only scan for the TIME_ZONE incident (lex-app rc212 – v2.1.3).

It answers one question for one database: which datetime values did the
incident store wrong, and how sure are we about each one?

The file needs only the standard library and a DB-API cursor (Django's, or
psycopg2 / psycopg when run on its own). It imports neither Django nor lex, so
it can be copied next to any instance and run against that instance's
database, whatever lex-app version the instance runs, without an upgrade:

    python scan.py --out ./scan-report        # credentials from the instance env
    python scan.py --dsn postgresql://... --out ./scan-clone

Inside a project, ``lex incident_tz scan`` runs the same code on Django's
connection.

The scan never writes. Callers open a READ ONLY transaction first, and
``scan()`` refuses to start in any other kind.

How it decides
--------------
1. Exposure, from the database's own audit log. An ``update`` audit row stores
   the record's ``edited_at`` in its payload exactly as the app's API rendered
   it, next to the row's own ``date``, which is always a correct instant.
   Pre-incident versions rendered Berlin wall-clock digits, affected versions
   UTC digits (v2.1.1 – v2.1.3 with a trailing ``Z``), fixed versions an
   explicit offset such as ``+02:00``. Each row gets one of those eras. Only
   updates made through the app are read: creates carry what the browser sent,
   deletes the record's previous edit, data loads their file's values. Digits
   without microseconds are unreadable: a calculation rewrites its click's row
   with a plain copy that drops them and writes UTC digits in every version,
   fixed ones included. A run of affected rows is one exposure period, kept as
   two brackets: it started after the last earlier row and by the first
   affected row, and it ended after the last affected row and by the next row.
   Where the era changes between a pre-incident and a fixed row, or the first
   row is fixed, the code in between is unknown. Run inside the instance, the
   lex-app version it runs counts as a reading at scan time. Stretches of more
   than a week without a readable row are listed for a check against the
   version history; a stated ``--window`` adds to the audit log's periods.
2. Census: every ``timestamp with time zone`` column in the scanned schemas,
   by layer: project table, history, meta-history, framework.
3. Value classes. A value with microseconds was written by a clock, whose zone
   always matched the reader's: correct (verdict A). Values without
   microseconds were entered by people or imports: midnight UTC (the
   signature of a date stored during the incident), local midnight (a date
   stored correctly) or another time of day.
4. Provenance: when was each value written? For tables with lex history, the
   versions of each record are walked; a value was written when its final run
   of equal values began (meta-history ``sys_from`` when present, else
   ``valid_from``). Without history, ``created_at`` and ``edited_at`` bracket
   the write. Nothing written before rc212 was released can be affected.
5. Verdicts. A clock-written. B midnight UTC written inside an exposure period;
   corrected to local midnight of the same date. C another time of day written
   inside a period; wrong only if the frontend sent naive local time then, so
   it needs that proof before any correction. D correct. E anything the
   evidence cannot settle; the owner decides.
"""

from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Optional, Sequence

try:  # Python 3.9+; every lex v2 image has it.
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment,misc]

SCAN_VERSION = "1"
UTC = dt.timezone.utc
DEFAULT_ZONE = "Europe/Berlin"
DEFAULT_SCHEMAS = ("public",)

# v2.0.0rc212, the first release carrying the cause line (f622c9c), was
# published on 2026-06-26 at 10:06:08 UTC. Nothing written earlier is affected.
RC212_RELEASED_AT = dt.datetime(2026, 6, 26, 10, 6, 8, tzinfo=UTC)

# An update audit row's ``date`` and the ``edited_at`` in its payload are taken
# moments apart; the offsets that separate the eras are an hour or more.
AUDIT_TOLERANCE = dt.timedelta(minutes=30)

# Rows the initial-data loader writes carry the loaded file's values, not the
# moment of the change.
DATA_LOAD_AUTHOR = "system (initial_data_upload)"

# Longer than this without a readable audit row, and the report asks for a
# check against the instance's version history.
SILENT_STRETCH = dt.timedelta(days=7)

# Tables of Django, lex and the third-party apps lex installs. Their values are
# reported, never corrected.
FRAMEWORK_PREFIXES = (
    "django_",
    "auth_",
    "audit_logging_",
    "generic_app_",
    "authentication_",
    "core_",
    "legacy_data_",
    "api_",
    "process_admin_",
    "utilities_",
    "react_",
    "oauth2_authcodeflow_",
    "rest_framework_",
)
FRAMEWORK_TABLES = frozenset({"default_cache", "oidc_cache"})

ERA_PRE = "pre"
ERA_AFFECTED = "affected"
ERA_FIXED = "fixed"
ERA_UNKNOWN = "unknown"
ERAS = (ERA_PRE, ERA_AFFECTED, ERA_FIXED, ERA_UNKNOWN)

LAYERS = ("main", "history", "meta_history", "framework")
VERDICTS = ("A", "B", "C", "D", "E")

IN = "in"
OUT = "out"
EDGE = "edge"
AMBIGUOUS = "ambiguous"
UNKNOWN = "unknown"

REASONS = {
    ("B", IN): "date stored at midnight UTC while affected code was writing",
    ("C", IN): "time of day written while affected code was writing; "
               "wrong only if the frontend then sent naive local time",
    ("D", OUT): "time of day written outside every exposure period",
    ("E", "midnight_utc", OUT): "midnight UTC written outside every exposure "
                                "period: not this incident, check the source",
    ("E", EDGE): "written in the gap around an exposure period's start or end",
    ("E", AMBIGUOUS): "the row was written both inside and outside an exposure "
                      "period; this field's own write time is unknown",
    ("E", UNKNOWN): "no history and no created_at/edited_at to date the write",
}


class ScanError(RuntimeError):
    """The scan cannot run safely as asked."""


# ---------------------------------------------------------------------------
# Exposure: which code wrote each audit row
# ---------------------------------------------------------------------------

_STAMP = re.compile(
    r"^(?P<y>\d{4})-(?P<mo>\d{2})-(?P<d>\d{2})[T ](?P<h>\d{2}):(?P<mi>\d{2})"
    r"(?::(?P<s>\d{2})(?:\.(?P<f>\d{1,6})\d*)?)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def parse_stamp(text: str) -> Optional[tuple[dt.datetime, Optional[int]]]:
    """Split an ISO-8601 string into naive digits and an offset in minutes.

    The offset is ``None`` for a naive string and ``0`` for ``Z``.
    """
    match = _STAMP.match(text.strip())
    if match is None:
        return None
    try:
        digits = dt.datetime(
            int(match["y"]), int(match["mo"]), int(match["d"]),
            int(match["h"]), int(match["mi"]), int(match["s"] or 0),
            int((match["f"] or "0").ljust(6, "0")),
        )
    except ValueError:
        return None
    suffix = match["tz"]
    if suffix is None:
        return digits, None
    if suffix == "Z":
        return digits, 0
    sign = 1 if suffix[0] == "+" else -1
    compact = suffix[1:].replace(":", "")
    return digits, sign * (int(compact[:2]) * 60 + int(compact[2:]))


def read_audit_stamp(
    text: Any,
    date_utc: dt.datetime,
    date_local: dt.datetime,
    tolerance: dt.timedelta = AUDIT_TOLERANCE,
) -> tuple[str, str]:
    """``(era, reason)``: which code rendered this audit payload's ``edited_at``?

    ``date_utc`` and ``date_local`` are the audit row's own ``date`` as naive
    wall clocks in UTC and in the source zone. Only the API writes microseconds,
    so digits without them name no version: a calculation's rewrite of the row
    writes UTC digits whatever version runs.
    """
    if not isinstance(text, str):
        return ERA_UNKNOWN, "no edited_at"
    match = _STAMP.match(text.strip())
    parsed = parse_stamp(text) if match else None
    if parsed is None:
        return ERA_UNKNOWN, "not a timestamp"
    digits, _offset = parsed
    suffix = match["tz"]
    if suffix is not None and suffix != "Z":
        # Only versions with time zones on write an offset, +00:00 included.
        return ERA_FIXED, "explicit offset"
    if match["f"] is None:
        return ERA_UNKNOWN, "no microseconds"
    near_utc = abs(digits - date_utc) <= tolerance
    near_local = abs(digits - date_local) <= tolerance
    if near_utc and near_local:
        return ERA_UNKNOWN, "matches both"
    if suffix == "Z":
        # v2.1.1 – v2.1.3 rendered their naive UTC values with a literal "Z".
        return (ERA_AFFECTED, "UTC digits with Z") if near_utc else (ERA_UNKNOWN, "Z without UTC digits")
    if near_local:
        return ERA_PRE, "local digits"
    if near_utc:
        return ERA_AFFECTED, "UTC digits"
    return ERA_UNKNOWN, "matches neither"


def audit_era(
    text: Any,
    date_utc: dt.datetime,
    date_local: dt.datetime,
    tolerance: dt.timedelta = AUDIT_TOLERANCE,
) -> str:
    """Which code rendered this audit payload's ``edited_at``? See ``read_audit_stamp``."""
    return read_audit_stamp(text, date_utc, date_local, tolerance)[0]


_VERSION = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-?rc(\d+))?$")


def era_of_version(version: Optional[str], deployment_target: Optional[str] = None) -> Optional[str]:
    """The era a lex-app version writes in, or ``None`` when it cannot be told.

    rc84 – rc211 and v1.x read and wrote Berlin time; rc212 – rc221, the
    2.0.0-azure build and v2.1.1 – v2.1.3 UTC; v2.1.4 and later have time zones
    on. On a deployment target other than GCP time zones were always on.
    """
    if not version:
        return None
    if deployment_target and deployment_target not in ("default", "GCP"):
        return ERA_FIXED
    text = str(version).strip().lower()
    if text.lstrip("v") == "2.0.0-azure":
        return ERA_AFFECTED
    match = _VERSION.match(text)
    if match is None:
        return None
    major, minor, patch = (int(match[i]) for i in (1, 2, 3))
    rc = int(match[4]) if match[4] else None
    if major < 2:
        return ERA_PRE
    if (major, minor, patch) == (2, 0, 0):
        if rc is None or rc < 84:
            return None
        if rc < 212:
            return ERA_PRE
        return ERA_AFFECTED if rc <= 221 else None
    if (major, minor) == (2, 0):
        return None  # side branches such as the 2.0.8 AI builds
    if (major, minor) == (2, 1):
        if patch >= 4:
            return ERA_FIXED
        return ERA_AFFECTED if patch >= 1 and rc is None else None
    return ERA_FIXED


def installed_lex_app_version() -> Optional[str]:
    """The lex-app version installed in this Python, if any."""
    try:
        from importlib import metadata

        return metadata.version("lex-app")
    except Exception:  # noqa: BLE001 - absent or unreadable metadata
        return None


def _aware(value: Optional[dt.datetime]) -> Optional[dt.datetime]:
    if value is None:
        return None
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass
class Period:
    """One stretch in which affected code wrote to this database.

    The writes known to come from affected code run from ``started_by`` to
    ``ended_after``. ``started_after`` is the last earlier write from other code
    and ``ended_by`` the next later one (``None``: still open, or no later
    write). Between a bracket's two ends the code that wrote is unknown.
    """

    started_by: dt.datetime
    ended_after: dt.datetime
    started_after: Optional[dt.datetime] = None
    ended_by: Optional[dt.datetime] = None
    affected_rows: int = 0
    source: str = "audit_log"

    @property
    def lower_edge(self) -> dt.datetime:
        if self.started_after is None:
            return RC212_RELEASED_AT
        return max(self.started_after, RC212_RELEASED_AT)

    def position(self, when: dt.datetime) -> str:
        if self.started_by <= when <= self.ended_after:
            return IN
        if self.lower_edge < when < self.started_by:
            return EDGE
        if when > self.ended_after and (self.ended_by is None or when < self.ended_by):
            return EDGE
        return OUT

    def touches(self, start: dt.datetime, end: dt.datetime) -> bool:
        """Does any part of this period, edges included, lie inside (start, end)?"""
        upper = self.ended_by if self.ended_by is not None else dt.datetime.max.replace(tzinfo=UTC)
        return start < upper and end > self.lower_edge

    def as_dict(self) -> dict:
        return {
            "started_after": _iso(self.started_after),
            "started_by": _iso(self.started_by),
            "ended_after": _iso(self.ended_after),
            "ended_by": _iso(self.ended_by),
            "affected_rows": self.affected_rows,
            "source": self.source,
        }


def exposure_periods(rows: Iterable[tuple]) -> list[Period]:
    """Turn readable audit rows ``(date, era)``, oldest first, into exposure periods.

    A third element ``False`` marks a reading that is not an audit row (the
    version running at scan time): it opens, extends or closes a period like a
    row but is not counted as one.
    """
    periods: list[Period] = []
    current: Optional[Period] = None
    last_other: Optional[dt.datetime] = None
    for row in rows:
        when, era = row[0], row[1]
        is_row = row[2] if len(row) > 2 else True
        if era == ERA_UNKNOWN:
            continue
        if era == ERA_AFFECTED:
            if current is None:
                current = Period(
                    started_by=when, ended_after=when, started_after=last_other,
                    source="audit_log" if is_row else "running_version",
                )
            else:
                current.ended_after = when
                if not is_row and current.source == "audit_log":
                    current.source = "audit_log+running_version"
            if is_row:
                current.affected_rows += 1
            continue
        if current is not None:
            current.ended_by = when
            periods.append(current)
            current = None
        last_other = when
    if current is not None:
        periods.append(current)
    return periods


@dataclass
class Gap:
    """A stretch in which the code changed with no affected row to date it.

    Its whole length is uncertain: an affected version may have run there.
    It answers ``position`` and ``touches`` like a ``Period``.
    """

    start: dt.datetime
    end: dt.datetime
    before: Optional[str]
    after: str

    def position(self, when: dt.datetime) -> str:
        return EDGE if self.start < when < self.end else OUT

    def touches(self, start: dt.datetime, end: dt.datetime) -> bool:
        return start < self.end and end > self.start

    def as_dict(self) -> dict:
        return {"start": _iso(self.start), "end": _iso(self.end),
                "before": self.before, "after": self.after}


def era_change_gaps(rows: Iterable[tuple]) -> list[Gap]:
    """Where the era changes between two readings that are not affected.

    A pre-incident row followed by a fixed one (or the reverse), and a first
    reading that is fixed, leave open whether an affected version ran in
    between. Next to affected rows a period's own edges already say so.
    """
    points = [(row[0], row[1]) for row in rows if row[1] != ERA_UNKNOWN]
    gaps: list[Gap] = []
    if points and points[0][1] == ERA_FIXED and points[0][0] > RC212_RELEASED_AT:
        gaps.append(Gap(RC212_RELEASED_AT, points[0][0], None, ERA_FIXED))
    for (start, before), (end, after) in zip(points, points[1:]):
        if before != after and ERA_AFFECTED not in (before, after) and end > RC212_RELEASED_AT:
            gaps.append(Gap(max(start, RC212_RELEASED_AT), end, before, after))
    return gaps


def silent_stretches(
    rows: Iterable[tuple], now: Optional[dt.datetime], minimum: dt.timedelta = SILENT_STRETCH,
) -> list[dict]:
    """Stretches after rc212's release longer than ``minimum`` with no readable row.

    With the same era on both sides the scan assumes the same code ran; a round
    trip through an affected version could hide there, so each one is listed
    for a check against the instance's version history.
    """
    times = sorted(row[0] for row in rows if row[1] != ERA_UNKNOWN and row[0] > RC212_RELEASED_AT)
    marks = [RC212_RELEASED_AT] + times
    if now is not None and now > RC212_RELEASED_AT:
        marks.append(now)
    stretches = []
    for start, end in zip(marks, marks[1:]):
        if end - start > minimum:
            stretches.append({
                "start": _iso(start), "end": _iso(end),
                "days": round((end - start).total_seconds() / 86400, 1),
            })
    return stretches


def manual_period(start: dt.datetime, end: dt.datetime) -> Period:
    """A period the operator states; it has no uncertain edges."""
    return Period(
        started_by=start, ended_after=end, started_after=start,
        ended_by=end, source="manual",
    )


def position_of(when: Optional[dt.datetime], periods: Sequence[Period]) -> str:
    if when is None:
        return UNKNOWN
    if when < RC212_RELEASED_AT:
        return OUT
    positions = {period.position(when) for period in periods}
    if IN in positions:
        return IN
    if EDGE in positions:
        return EDGE
    return OUT


def span_position(
    start: Optional[dt.datetime], end: Optional[dt.datetime], periods: Sequence[Period]
) -> str:
    """Where a value written at some moment between ``start`` and ``end`` falls."""
    if start is None or end is None:
        return UNKNOWN
    if start > end:
        start, end = end, start
    for period in periods:
        if period.position(start) == IN and period.position(end) == IN:
            return IN
    if position_of(start, periods) == OUT and position_of(end, periods) == OUT:
        if not any(period.touches(start, end) for period in periods):
            return OUT
    return AMBIGUOUS


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def verdict(kind: str, position: str) -> tuple[str, str]:
    """``(verdict, reason)`` for a value without microseconds."""
    if kind == "midnight_utc":
        if position == IN:
            return "B", REASONS[("B", IN)]
        if position == OUT:
            return "E", REASONS[("E", "midnight_utc", OUT)]
        return "E", REASONS[("E", position)]
    if position == IN:
        return "C", REASONS[("C", IN)]
    if position == OUT:
        return "D", REASONS[("D", OUT)]
    return "E", REASONS[("E", position)]


def dst_ambiguous(digits: dt.datetime, zone: str) -> bool:
    """Do these wall-clock digits name zero or two instants in ``zone``?"""
    if ZoneInfo is None:
        return False
    tz = ZoneInfo(zone)
    first = digits.replace(tzinfo=tz, fold=0).utcoffset()
    second = digits.replace(tzinfo=tz, fold=1).utcoffset()
    return first != second


# ---------------------------------------------------------------------------
# Census
# ---------------------------------------------------------------------------


def quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def layer_of(table: str, framework_tables: frozenset = frozenset()) -> str:
    if table in framework_tables or table in FRAMEWORK_TABLES:
        return "framework"
    if table.startswith(FRAMEWORK_PREFIXES):
        return "framework"
    if table.endswith("_meta_history"):
        return "meta_history"
    if "_historical" in table:
        return "history"
    return "main"


@dataclass
class ColumnReport:
    schema: str
    table: str
    column: str
    layer: str
    values: int = 0
    clock: int = 0
    midnight_utc: int = 0
    midnight_local: int = 0
    time_of_day: int = 0
    r11_mirror: int = 0
    first: Optional[str] = None
    last: Optional[str] = None
    provenance: str = "none"
    verdicts: dict = field(default_factory=lambda: dict.fromkeys(VERDICTS, 0))


@dataclass
class _Table:
    schema: str
    name: str
    columns: set
    primary_key: Optional[str]

    @property
    def sql(self) -> str:
        return f"{quote(self.schema)}.{quote(self.name)}"

    def key(self, alias: str) -> str:
        """The row identifier, as an expression on ``alias``."""
        if self.primary_key:
            return f"{alias}.{quote(self.primary_key)}"
        return f"{alias}.ctid::text"


def _fetchall(cursor, sql: str, params: Any = None) -> list:
    cursor.execute(sql, params or {})
    return list(cursor.fetchall())


def _load_tables(cursor, schemas: Sequence[str]) -> dict:
    rows = _fetchall(
        cursor,
        """
        SELECT c.table_schema, c.table_name, c.column_name
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE t.table_type = 'BASE TABLE' AND c.table_schema = ANY(%(schemas)s)
        """,
        {"schemas": list(schemas)},
    )
    tables: dict = {}
    for schema, table, column in rows:
        tables.setdefault((schema, table), _Table(schema, table, set(), None)).columns.add(column)
    keys = _fetchall(
        cursor,
        """
        SELECT tc.table_schema, tc.table_name, min(k.column_name), count(*)
          FROM information_schema.table_constraints tc
          JOIN information_schema.key_column_usage k
            ON k.constraint_name = tc.constraint_name
           AND k.table_schema = tc.table_schema AND k.table_name = tc.table_name
         WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_schema = ANY(%(schemas)s)
         GROUP BY tc.table_schema, tc.table_name
        """,
        {"schemas": list(schemas)},
    )
    for schema, table, column, count in keys:
        if (schema, table) in tables and count == 1:
            tables[(schema, table)].primary_key = column
    return tables


def _datetime_columns(cursor, schemas: Sequence[str]) -> list:
    return _fetchall(
        cursor,
        """
        SELECT c.table_schema, c.table_name, c.column_name, c.data_type
          FROM information_schema.columns c
          JOIN information_schema.tables t
            ON t.table_schema = c.table_schema AND t.table_name = c.table_name
         WHERE t.table_type = 'BASE TABLE' AND c.table_schema = ANY(%(schemas)s)
           AND c.data_type IN ('timestamp with time zone', 'timestamp without time zone')
         ORDER BY c.table_schema, c.table_name, c.ordinal_position
        """,
        {"schemas": list(schemas)},
    )


# Value-class expressions over one value expression ``v``. ``%(zone)s`` is
# the source zone. ``mod`` keeps literal percent signs out of the SQL.
def _classes(v: str) -> dict:
    micro = f"mod(date_part('microseconds', {v} AT TIME ZONE 'UTC')::bigint, 1000000)"
    utc_time = f"({v} AT TIME ZONE 'UTC')::time"
    local_time = f"({v} AT TIME ZONE %(zone)s)::time"
    offset = f"(({v} AT TIME ZONE %(zone)s) - ({v} AT TIME ZONE 'UTC'))"
    return {
        "clock": f"{micro} <> 0",
        "midnight_utc": f"{micro} = 0 AND {utc_time} = time '00:00'",
        "midnight_local": (
            f"{micro} = 0 AND {local_time} = time '00:00' AND {utc_time} <> time '00:00'"
        ),
        "time_of_day": (
            f"{micro} = 0 AND {utc_time} <> time '00:00' AND {local_time} <> time '00:00'"
        ),
        "r11_mirror": f"{micro} = 0 AND {local_time} = time '00:00' - {offset}",
        # Values whose write time decides their verdict.
        "candidate": (
            f"{micro} = 0 AND ({utc_time} = time '00:00' OR {local_time} <> time '00:00')"
        ),
        "kind": (
            f"CASE WHEN {utc_time} = time '00:00' THEN 'midnight_utc' ELSE 'time_of_day' END"
        ),
        "digits": f"({v} AT TIME ZONE 'UTC')",
        "fix_b": f"(date_trunc('day', {v} AT TIME ZONE 'UTC') AT TIME ZONE %(zone)s)",
        "fix_c": f"(({v} AT TIME ZONE 'UTC') AT TIME ZONE %(zone)s)",
    }


def _column_stats(cursor, table: _Table, column: str, zone: str) -> tuple:
    v = quote(column)
    c = _classes(v)
    sql = f"""
        SELECT count({v}),
               count(*) FILTER (WHERE {c['clock']}),
               count(*) FILTER (WHERE {c['midnight_utc']}),
               count(*) FILTER (WHERE {c['midnight_local']}),
               count(*) FILTER (WHERE {c['time_of_day']}),
               count(*) FILTER (WHERE {c['r11_mirror']}),
               min({v}), max({v})
          FROM {table.sql}
    """
    cursor.execute(sql, {"zone": zone})
    return cursor.fetchone()


# ---------------------------------------------------------------------------
# Provenance: when was each value written?
# ---------------------------------------------------------------------------


def _runs_cte(history: _Table, meta: Optional[_Table], column: str, record_key: str) -> str:
    """Versions of each record, grouped into runs of equal values.

    ``written_at`` is when the run a version belongs to began: the moment the
    value it holds was written. ``t`` is when the version itself was recorded.
    """
    col = quote(column)
    rid = quote(record_key)
    if meta is not None:
        versions = f"""
            SELECT h."history_id" AS hid, h.{rid} AS rid, h.{col} AS val,
                   COALESCE(ms.first_sys, h."valid_from") AS t
              FROM {history.sql} h
              LEFT JOIN (SELECT "history_object_id" AS hid, min("sys_from") AS first_sys
                           FROM {meta.sql} GROUP BY "history_object_id") ms
                ON ms.hid = h."history_id"
        """
    else:
        versions = f"""
            SELECT h."history_id" AS hid, h.{rid} AS rid, h.{col} AS val,
                   h."valid_from" AS t
              FROM {history.sql} h
        """
    return f"""
        WITH versions AS ({versions}),
        marked AS (
            SELECT *, CASE WHEN val IS NOT DISTINCT FROM
                               lag(val) OVER (PARTITION BY rid ORDER BY t, hid)
                           THEN 0 ELSE 1 END AS chg
              FROM versions
        ),
        grouped AS (
            SELECT *, sum(chg) OVER (PARTITION BY rid ORDER BY t, hid
                                     ROWS UNBOUNDED PRECEDING) AS grp
              FROM marked
        ),
        runs AS (
            SELECT hid, rid, val, t,
                   min(t) OVER (PARTITION BY rid, grp) AS written_at,
                   row_number() OVER (PARTITION BY rid ORDER BY t DESC, hid DESC) AS rn
              FROM grouped
        )
    """


def _history_ready(history: Optional[_Table], column: str, record_key: Optional[str]) -> bool:
    return (
        history is not None
        and record_key is not None
        and {"history_id", "valid_from", column, record_key} <= history.columns
    )


def _meta_ready(meta: Optional[_Table], column: str) -> bool:
    return meta is not None and {"history_object_id", "sys_from", column} <= meta.columns


# ---------------------------------------------------------------------------
# The scan
# ---------------------------------------------------------------------------


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    value = _aware(value)
    return value.isoformat().replace("+00:00", "Z") if value is not None else None


def _find_audit_tables(tables: dict) -> list:
    found = []
    for (schema, name), table in sorted(tables.items()):
        if name.endswith("auditlog") and {"date", "payload"} <= table.columns:
            found.append(table)
    return found


def _audit_rows(cursor, table: _Table, zone: str) -> list:
    action = '"action"' if "action" in table.columns else "NULL::text"
    author = '"author"' if "author" in table.columns else "NULL::text"
    cursor.execute(
        f"""
        SELECT "date", ("date" AT TIME ZONE 'UTC'), ("date" AT TIME ZONE %(zone)s),
               ("payload"->>'edited_at'), {action}, {author}
          FROM {table.sql}
         WHERE ("payload"->>'edited_at') IS NOT NULL
         ORDER BY "date"
        """,
        {"zone": zone},
    )
    return list(cursor.fetchall())


def detect_exposure(
    cursor, tables: dict, zone: str,
    running_era: Optional[str] = None, now: Optional[dt.datetime] = None,
) -> dict:
    """Periods, gaps and silent stretches, from the audit log and the running version."""
    eras = dict.fromkeys(ERAS, 0)
    skipped = {"create": 0, "delete": 0, "other_action": 0, "data_load": 0}
    unreadable: collections.Counter = collections.Counter()
    anomalies: list = []
    classified: list = []
    audit_tables = _find_audit_tables(tables)
    for table in audit_tables:
        for when, date_utc, date_local, text, action, author in _audit_rows(cursor, table, zone):
            if action is not None and action != "update":
                skipped[action if action in ("create", "delete") else "other_action"] += 1
                continue
            if author == DATA_LOAD_AUTHOR:
                skipped["data_load"] += 1
                continue
            when = _aware(when)
            era, why = read_audit_stamp(text, date_utc, date_local)
            if era == ERA_AFFECTED and when < RC212_RELEASED_AT:
                # Affected code did not exist yet: a misread, not a period.
                anomalies.append(when)
                era, why = ERA_UNKNOWN, "affected before rc212's release"
            eras[era] += 1
            if era == ERA_UNKNOWN:
                unreadable[why] += 1
            classified.append((when, era))
    classified.sort(key=lambda item: item[0])
    readable = [item for item in classified if item[1] != ERA_UNKNOWN]
    points = list(readable)
    if running_era in (ERA_PRE, ERA_AFFECTED, ERA_FIXED) and now is not None:
        points.append((now, running_era, False))
    return {
        "source": "audit_log" if audit_tables else "none",
        "audit_tables": [t.name for t in audit_tables],
        "eras": eras,
        "unreadable": dict(unreadable),
        "skipped": skipped,
        "anomalies": [_iso(when) for when in sorted(anomalies)],
        # The audit log only speaks for the time its readable rows cover.
        "readable_rows": len(readable),
        "first_readable": _iso(readable[0][0]) if readable else None,
        "last_readable": _iso(readable[-1][0]) if readable else None,
        "periods": exposure_periods(points),
        "gaps": era_change_gaps(points),
        "silent_stretches": silent_stretches(readable, now),
    }


def _row(layer, table, column, row_id, record_id, value, digits, kind,
         fix_b, fix_c, written, position, zone) -> dict:
    letter, reason = verdict(kind, position)
    if isinstance(written, tuple):
        written_out: Any = [_iso(written[0]), _iso(written[1])]
    else:
        written_out = _iso(written)
    return {
        "layer": layer,
        "table": table,
        "column": column,
        "row": None if row_id is None else str(row_id),
        "record": None if record_id is None else str(record_id),
        "value": _iso(value),
        "kind": kind,
        "written": written_out,
        "position": position,
        "verdict": letter,
        "corrected": _iso(fix_b if letter == "B" else fix_c if letter == "C" else None),
        "dst_ambiguous": bool(kind == "time_of_day" and digits is not None
                              and dst_ambiguous(digits, zone)),
        "reason": reason,
    }


def _classify_main(cursor, table, column, history, meta, periods, zone, layer) -> tuple:
    c = _classes("t." + quote(column))
    record_key = table.primary_key
    has_bracket = {"created_at", "edited_at"} <= table.columns
    created = 't."created_at"' if has_bracket else "NULL::timestamptz"
    edited = 't."edited_at"' if has_bracket else "NULL::timestamptz"
    col = quote(column)
    if layer != "framework" and _history_ready(history, column, record_key):
        meta = meta if _meta_ready(meta, column) else None
        sql = _runs_cte(history, meta, column, record_key) + f"""
            SELECT t.{quote(record_key)}, t.{col}, {c['digits']}, {c['kind']},
                   {c['fix_b']}, {c['fix_c']}, r.written_at,
                   (r.rid IS NOT NULL AND t.{col} IS NOT DISTINCT FROM r.val),
                   {created}, {edited}
              FROM {table.sql} t
              LEFT JOIN runs r ON r.rid = t.{quote(record_key)} AND r.rn = 1
             WHERE {c['candidate']}
        """
        provenance = "history"
    else:
        sql = f"""
            SELECT {table.key('t')}, t.{col}, {c['digits']}, {c['kind']},
                   {c['fix_b']}, {c['fix_c']}, NULL::timestamptz, FALSE, {created}, {edited}
              FROM {table.sql} t
             WHERE {c['candidate']}
        """
        provenance = "bracket" if has_bracket else "none"
    cursor.execute(sql, {"zone": zone})
    rows = []
    for row_id, value, digits, kind, fix_b, fix_c, written_at, matched, c_at, e_at in cursor.fetchall():
        if layer == "framework":
            written: Any = None
            position = UNKNOWN
        elif matched:
            written = _aware(written_at)
            position = position_of(written, periods)
        elif c_at is not None and e_at is not None:
            written = (_aware(c_at), _aware(e_at))
            position = span_position(written[0], written[1], periods)
        else:
            written = None
            position = UNKNOWN
        rows.append(_row(layer, table.name, column, row_id, None, value, digits, kind,
                         fix_b, fix_c, written, position, zone))
    return rows, provenance


def _classify_history(cursor, history, meta, column, record_key, periods, zone) -> list:
    meta = meta if _meta_ready(meta, column) else None
    c = _classes("r.val")
    sql = _runs_cte(history, meta, column, record_key) + f"""
        SELECT r.hid, r.rid, r.val, {c['digits']}, {c['kind']}, {c['fix_b']}, {c['fix_c']},
               r.written_at
          FROM runs r
         WHERE {c['candidate']}
    """
    cursor.execute(sql, {"zone": zone})
    rows = []
    for hid, rid, value, digits, kind, fix_b, fix_c, written_at in cursor.fetchall():
        written = _aware(written_at)
        rows.append(_row("history", history.name, column, hid, rid, value, digits, kind,
                         fix_b, fix_c, written, position_of(written, periods), zone))
    return rows


def _classify_meta(cursor, meta, history, column, record_key, periods, zone) -> list:
    c = _classes("m." + quote(column))
    col = quote(column)
    sql = _runs_cte(history, meta, column, record_key) + f"""
        SELECT {meta.key('m')}, m."history_object_id", m.{col}, {c['digits']}, {c['kind']},
               {c['fix_b']}, {c['fix_c']}, r.written_at,
               (r.hid IS NOT NULL AND m.{col} IS NOT DISTINCT FROM r.val),
               r.t, m."sys_from"
          FROM {meta.sql} m
          LEFT JOIN runs r ON r.hid = m."history_object_id"
         WHERE {c['candidate']}
    """
    cursor.execute(sql, {"zone": zone})
    rows = []
    for (meta_id, hid, value, digits, kind, fix_b, fix_c, written_at, matched,
         first_recorded, recorded) in cursor.fetchall():
        if matched:
            written: Any = _aware(written_at)
            position = position_of(written, periods)
        else:
            # The history row changed after this copy was taken: the value was
            # written between the history row's first record and this copy.
            written = (_aware(first_recorded), _aware(recorded))
            position = span_position(written[0], written[1], periods)
        rows.append(_row("meta_history", meta.name, column, meta_id, hid, value, digits,
                         kind, fix_b, fix_c, written, position, zone))
    return rows


def _signature_only(cursor, table, column, zone, layer) -> list:
    c = _classes("t." + quote(column))
    cursor.execute(
        f"""
        SELECT {table.key('t')}, t.{quote(column)}, {c['digits']}, {c['kind']},
               {c['fix_b']}, {c['fix_c']}
          FROM {table.sql} t
         WHERE {c['candidate']}
        """,
        {"zone": zone},
    )
    return [
        _row(layer, table.name, column, row_id, None, value, digits, kind, fix_b, fix_c,
             None, UNKNOWN, zone)
        for row_id, value, digits, kind, fix_b, fix_c in cursor.fetchall()
    ]


def _is_read_only(cursor) -> bool:
    cursor.execute("SHOW transaction_read_only")
    return str(cursor.fetchone()[0]).lower() == "on"


def scan(
    cursor,
    *,
    zone: str = DEFAULT_ZONE,
    schemas: Sequence[str] = DEFAULT_SCHEMAS,
    windows: Sequence[tuple[dt.datetime, dt.datetime]] = (),
    tables: Optional[Iterable[str]] = None,
    framework_tables: Iterable[str] = (),
    expected_columns: Optional[Iterable[tuple[str, str]]] = None,
    database: Optional[str] = None,
    audit_periods: bool = True,
    running_version: Optional[str] = None,
    deployment_target: Optional[str] = None,
) -> dict:
    """Scan the database behind ``cursor`` and return the report as a dict.

    ``windows`` are periods the operator states, from the version history; they
    add to the audit log's periods, or replace them with ``audit_periods=False``
    (the audit log's view is still reported). ``running_version`` is the lex-app
    version the instance runs now, read at the scan's start; with
    ``deployment_target`` it says which era is writing at that moment.
    ``tables`` limits the classification to those table names (the audit log is
    always read). ``expected_columns`` is the ``(table, column)`` list of
    DateTimeFields the app's models declare; the report lists any that the
    database lacks and any database column no model declares.
    """
    if not _is_read_only(cursor):
        raise ScanError(
            "The scan only runs inside a READ ONLY transaction. Run "
            "'SET TRANSACTION READ ONLY' first, or use the command-line entry points, "
            "which do it for you."
        )
    framework = frozenset(framework_tables)
    started = dt.datetime.now(tz=UTC)
    running_era = era_of_version(running_version, deployment_target)
    catalogue = _load_tables(cursor, schemas)
    exposure = detect_exposure(cursor, catalogue, zone, running_era=running_era, now=started)
    stated = [manual_period(_aware(a), _aware(b)) for a, b in windows]
    if audit_periods:
        periods: list = exposure["periods"] + stated
        gaps: list = exposure["gaps"]
        if stated:
            base = exposure["source"]
            exposure["source"] = "manual" if base == "none" else f"{base}+manual"
    else:
        exposure["audit_periods"] = [p.as_dict() for p in exposure["periods"]]
        exposure["audit_gaps"] = [g.as_dict() for g in exposure["gaps"]]
        exposure["source"] = "manual"
        periods, gaps = stated, []
    exposure["periods"] = [p.as_dict() for p in periods]
    exposure["gaps"] = [g.as_dict() for g in gaps]
    # Gaps answer position() like periods: one list places every write.
    periods = periods + gaps

    # Which tables belong together: <app>_<model>, its history
    # <app>_historical<model>, and the meta-history, which lex names after the
    # main table (<app>_<model>_meta_history); the history table's name is
    # accepted too.
    by_name = {name: table for (_schema, name), table in catalogue.items()}
    history_of: dict = {}
    for name in by_name:
        if layer_of(name, framework) == "history":
            base = name.replace("_historical", "_", 1)
            if base in by_name:
                history_of[base] = by_name[name]
    main_of_history = {history.name: by_name[main] for main, history in history_of.items()}

    def meta_for(main_name: str) -> Optional[_Table]:
        history = history_of.get(main_name)
        for candidate in (main_name, history.name if history is not None else None):
            if candidate is not None and candidate + "_meta_history" in by_name:
                return by_name[candidate + "_meta_history"]
        return None

    owners_of_meta = {}
    for main_name, history in history_of.items():
        meta = meta_for(main_name)
        if meta is not None:
            owners_of_meta[meta.name] = (by_name[main_name], history)

    wanted = set(tables) if tables is not None else None
    columns: list[ColumnReport] = []
    rows: list[dict] = []
    naive_columns: list[str] = []
    for schema, table_name, column, data_type in _datetime_columns(cursor, schemas):
        if data_type != "timestamp with time zone":
            naive_columns.append(f"{table_name}.{column}")
            continue
        if wanted is not None and table_name not in wanted:
            continue
        table = catalogue[(schema, table_name)]
        layer = layer_of(table_name, framework)
        stats = _column_stats(cursor, table, column, zone)
        report = ColumnReport(
            schema, table_name, column, layer, *stats[:6],
            first=_iso(stats[6]), last=_iso(stats[7]),
        )
        report.verdicts["A"] = report.clock
        report.verdicts["D"] = report.midnight_local
        found: list[dict] = []
        if report.midnight_utc or report.time_of_day:
            if layer in ("main", "framework"):
                found, report.provenance = _classify_main(
                    cursor, table, column, history_of.get(table_name),
                    meta_for(table_name), periods, zone, layer,
                )
                if layer == "framework":
                    report.provenance = "none"
            elif layer == "history":
                main = main_of_history.get(table_name)
                key = main.primary_key if main is not None else "id"
                meta = (meta_for(main.name) if main is not None
                        else by_name.get(table_name + "_meta_history"))
                if _history_ready(table, column, key):
                    found = _classify_history(cursor, table, meta, column, key, periods, zone)
                    report.provenance = "history"
                else:
                    found = _signature_only(cursor, table, column, zone, layer)
            else:  # meta_history
                main, history = owners_of_meta.get(table_name, (None, None))
                key = main.primary_key if main is not None else "id"
                if _history_ready(history, column, key) and _meta_ready(table, column):
                    found = _classify_meta(cursor, table, history, column, key, periods, zone)
                    report.provenance = "history"
                else:
                    found = _signature_only(cursor, table, column, zone, layer)
        for row in found:
            report.verdicts[row["verdict"]] += 1
        rows.extend(found)
        columns.append(report)

    totals = {layer: dict.fromkeys(VERDICTS, 0) for layer in LAYERS}
    for report in columns:
        for letter, count in report.verdicts.items():
            totals[report.layer][letter] += count

    reconciliation = None
    if expected_columns is not None:
        expected = {(t, c) for t, c in expected_columns}
        present = {
            (table_name, column)
            for _schema, table_name, column, data_type in _datetime_columns(cursor, schemas)
            if data_type == "timestamp with time zone"
        }
        reconciliation = {
            "declared_but_missing": sorted(f"{t}.{c}" for t, c in expected - present),
            "present_but_undeclared": sorted(f"{t}.{c}" for t, c in present - expected),
        }

    return {
        "meta": {
            "scan_version": SCAN_VERSION,
            "database": database,
            "scanned_at": _iso(started),
            "zone": zone,
            "schemas": list(schemas),
            "tables_filter": sorted(wanted) if wanted is not None else None,
            "read_only": True,
            "running_version": running_version,
            "running_era": running_era,
            "deployment_target": deployment_target,
        },
        "exposure": exposure,
        "census": {
            "columns": len(columns),
            "tables": len({(c.schema, c.table) for c in columns}),
            "naive_timestamp_columns": naive_columns,
            "reconciliation": reconciliation,
        },
        "totals": totals,
        "columns": [asdict(c) for c in columns],
        "rows": rows,
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------


def _md_table(header: Sequence[str], body: Iterable[Sequence[Any]]) -> list:
    lines = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    for row in body:
        lines.append("| " + " | ".join("" if v is None else str(v) for v in row) + " |")
    return lines


def render_markdown(report: dict, max_rows: int = 50) -> str:
    meta, exposure = report["meta"], report["exposure"]
    out = [
        f"# Timezone-incident scan: {meta['database'] or 'database'}",
        "",
        f"Scanned {meta['scanned_at']}, read-only, zone {meta['zone']}, "
        f"schemas {', '.join(meta['schemas'])}.",
        "",
        "## Exposure",
        "",
    ]
    running = meta.get("running_version")
    if running:
        out += [f"Running at scan time: lex-app {running} ({meta.get('running_era') or 'era unknown'}).", ""]
    else:
        out += ["Running at scan time: not known (the scan ran from outside the instance; "
                "pass --running-version).", ""]
    if exposure["periods"]:
        out.append(
            f"{len(exposure['periods'])} period(s) of writes by affected code "
            f"(source: {exposure['source']}). Values written between a bracket's two ends "
            "are reported as E."
        )
        out.append("")
        out += _md_table(
            ["#", "Started after", "Started by", "Ended after", "Ended by", "Affected rows", "Source"],
            [
                (i + 1, p["started_after"] or "rc212 release", p["started_by"],
                 p["ended_after"], p["ended_by"] or "open", p["affected_rows"], p.get("source", ""))
                for i, p in enumerate(exposure["periods"])
            ],
        )
    elif exposure["source"] == "none":
        out.append("No audit log was found, so no exposure could be dated. "
                   "State the period with --window, from the instance's version history.")
    elif not exposure["readable_rows"]:
        out.append("The audit log has no update row this scan can read, so it cannot date "
                   "any exposure. State the period with --window, from the instance's "
                   "version history.")
    else:
        out.append(
            f"The audit log's readable rows run from {exposure['first_readable']} to "
            f"{exposure['last_readable']}, and none of them was written by affected code. "
            "Exposure outside that range cannot be ruled out from the audit log."
        )
    if exposure.get("gaps"):
        out += ["", "The code changed between these readings with no row from affected code "
                    "to date it; an affected version may have run there, so values written in "
                    "between are reported as E.", ""]
        out += _md_table(
            ["From", "To", "Before", "After"],
            [(g["start"], g["end"], g["before"] or "rc212 release", g["after"])
             for g in exposure["gaps"]],
        )
    eras = exposure["eras"]
    reasons = ", ".join(f"{why} {count}" for why, count in sorted(exposure.get("unreadable", {}).items()))
    out += ["", f"Audit rows read (updates): pre-incident {eras['pre']}, affected {eras['affected']}, "
                f"fixed {eras['fixed']}, unreadable {eras['unknown']}"
                + (f" ({reasons})" if reasons else "") + "."]
    skipped = exposure.get("skipped") or {}
    if any(skipped.values()):
        out.append(
            "Skipped, because they do not hold the moment of the change: "
            f"creates {skipped.get('create', 0)}, deletes {skipped.get('delete', 0)}, "
            f"data loads {skipped.get('data_load', 0)}, other actions {skipped.get('other_action', 0)}."
        )
    if exposure.get("anomalies"):
        shown = ", ".join(exposure["anomalies"][:5])
        out.append(
            f"**Anomalies:** {len(exposure['anomalies'])} row(s) read as affected but dated before "
            f"rc212's release, which cannot be: {shown}. They open no period; check them."
        )
    if exposure.get("silent_stretches"):
        out += ["", f"Silent stretches: no readable audit row for more than {SILENT_STRETCH.days} days. "
                    "Check each against the instance's version history and pass any that "
                    "overlaps an affected version with --window.", ""]
        out += _md_table(
            ["From", "To", "Days"],
            [(st["start"], st["end"], st["days"]) for st in exposure["silent_stretches"]],
        )
    out.append("")

    out += ["## Verdicts", ""]
    out += _md_table(
        ["Layer", "A clock", "B date wrong", "C time to prove", "D correct", "E owner decides"],
        [(layer, *report["totals"][layer].values()) for layer in LAYERS],
    )
    out += ["", "Framework tables are reported, never corrected.", ""]

    flagged = [c for c in report["columns"]
               if c["layer"] != "framework" and (c["verdicts"]["B"] or c["verdicts"]["C"]
                                                 or c["verdicts"]["E"])]
    out += ["## Columns with B, C or E values", ""]
    if flagged:
        out += _md_table(
            ["Table", "Column", "Layer", "Values", "B", "C", "E", "Provenance"],
            [(c["table"], c["column"], c["layer"], c["values"], c["verdicts"]["B"],
              c["verdicts"]["C"], c["verdicts"]["E"], c["provenance"]) for c in flagged],
        )
    else:
        out.append("None.")
    out.append("")

    titles = {
        "B": "B: dates stored at midnight UTC by affected code",
        "C": "C: times written by affected code (need frontend proof)",
        "E": "E: values the evidence cannot settle",
    }
    project_rows = [r for r in report["rows"] if r["layer"] != "framework"]
    for letter in ("B", "C", "E"):
        picked = [r for r in project_rows if r["verdict"] == letter]
        out += [f"## {titles[letter]}", ""]
        if not picked:
            out += ["None.", ""]
            continue
        if letter == "E":
            body = [(r["table"], r["column"], r["row"], r["value"], r["written"], r["reason"])
                    for r in picked[:max_rows]]
            out += _md_table(["Table", "Column", "Row", "Value (UTC)", "Written", "Why"], body)
        else:
            body = [(r["table"], r["column"], r["row"], r["value"], r["written"], r["corrected"],
                     "yes" if r["dst_ambiguous"] else "") for r in picked[:max_rows]]
            out += _md_table(
                ["Table", "Column", "Row", "Value (UTC)", "Written", "Corrected", "DST hour"],
                body,
            )
        if len(picked) > max_rows:
            out.append(f"\n{len(picked) - max_rows} more in scan.json.")
        out.append("")

    recon = report["census"]["reconciliation"]
    if recon is not None:
        out += ["## Census reconciliation", ""]
        out.append("Declared by a model but missing in the database: "
                   + (", ".join(recon["declared_but_missing"]) or "none") + ".")
        out.append("In the database but declared by no model: "
                   + (", ".join(recon["present_but_undeclared"]) or "none") + ".")
        out.append("")
    if report["census"]["naive_timestamp_columns"]:
        out += ["Columns without a time zone (not scanned): "
                + ", ".join(report["census"]["naive_timestamp_columns"]) + ".", ""]
    return "\n".join(out)


def to_json(report: dict) -> str:
    return json.dumps(report, indent=2, default=str)


def write_report(report: dict, out_dir: str) -> tuple[str, str]:
    os.makedirs(out_dir, exist_ok=True)
    json_path = os.path.join(out_dir, "scan.json")
    md_path = os.path.join(out_dir, "scan.md")
    with open(json_path, "w", encoding="utf-8") as handle:
        handle.write(to_json(report))
    with open(md_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(report))
    return json_path, md_path


# ---------------------------------------------------------------------------
# Running on its own
# ---------------------------------------------------------------------------


def connection_settings_from_env(env: Optional[dict] = None) -> dict:
    """The database a deployed instance uses, from the variables its pods carry."""
    env = os.environ if env is None else env
    missing = [name for name in ("DATABASE_NAME", "POSTGRES_PASSWORD", "DATABASE_DOMAIN")
               if not env.get(name)]
    if missing:
        raise ScanError("Missing environment variables: " + ", ".join(missing)
                        + ". Pass --dsn instead.")
    return {
        "dbname": env["DATABASE_NAME"],
        "user": env.get("POSTGRES_USERNAME", "django"),
        "password": env["POSTGRES_PASSWORD"],
        "host": env["DATABASE_DOMAIN"],
        "port": int(env.get("DATABASE_PORT", "5432")),
    }


def parse_window(text: str) -> tuple[dt.datetime, dt.datetime]:
    """``START/END`` in ISO 8601; a value without an offset is read as UTC."""
    try:
        start_text, end_text = text.split("/", 1)
        parsed = []
        for part in (start_text, end_text):
            stamp = parse_stamp(part)
            if stamp is None:
                raise ValueError(part)
            digits, offset = stamp
            parsed.append((digits - dt.timedelta(minutes=offset or 0)).replace(tzinfo=UTC))
    except ValueError as exc:
        raise ScanError(f"Invalid --window {text!r}: use START/END in ISO 8601") from exc
    if parsed[1] <= parsed[0]:
        raise ScanError(f"Invalid --window {text!r}: END must be after START")
    return parsed[0], parsed[1]


def _connect(dsn: Optional[str]):
    try:
        import psycopg2 as driver  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - images ship psycopg2
        import psycopg as driver  # type: ignore[import-not-found,no-redef]
    connection = driver.connect(dsn) if dsn else driver.connect(**connection_settings_from_env())
    if hasattr(connection, "set_session"):
        connection.set_session(readonly=True, autocommit=False)
    else:  # pragma: no cover - psycopg 3
        connection.read_only = True
    return connection


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dsn", help="libpq connection string; default: the instance env")
    parser.add_argument("--out", help="folder for scan.json and scan.md")
    parser.add_argument("--json-stdout", action="store_true",
                        help="print the JSON report to stdout instead of writing files")
    parser.add_argument("--zone", default=DEFAULT_ZONE)
    parser.add_argument("--schema", action="append", dest="schemas")
    parser.add_argument("--table", action="append", dest="tables")
    parser.add_argument("--window", action="append", default=[],
                        help="START/END of an exposure period, from the version history; "
                             "adds to the audit log's periods")
    parser.add_argument("--window-only", action="store_true",
                        help="apply only the --window periods; the audit log's are still reported")
    parser.add_argument("--running-version",
                        help="lex-app version the instance runs; read from this Python "
                             "when the scan uses the instance's own settings (no --dsn)")
    parser.add_argument("--render", metavar="SCAN_JSON",
                        help="write scan.md from a saved JSON report; no database needed")
    args = parser.parse_args(argv)
    if args.window_only and not args.window:
        parser.error("--window-only needs at least one --window")
    if args.render:
        # A scan run inside a pod prints JSON; the Markdown is made here, locally.
        with open(args.render, encoding="utf-8") as handle:
            report = json.load(handle)
        json_path, md_path = write_report(
            report, args.out or os.path.dirname(os.path.abspath(args.render))
        )
        sys.stderr.write(f"Wrote {md_path}\n")
        return 0
    running_version = args.running_version
    deployment_target = None
    if not args.dsn:
        # Inside the instance: its own lex-app and settings are the running ones.
        running_version = running_version or installed_lex_app_version()
        deployment_target = os.environ.get("DATABASE_DEPLOYMENT_TARGET")
    connection = _connect(args.dsn)
    try:
        cursor = connection.cursor()
        cursor.execute("SET LOCAL lock_timeout = '5s'")
        report = scan(
            cursor,
            zone=args.zone,
            schemas=tuple(args.schemas or DEFAULT_SCHEMAS),
            windows=[parse_window(w) for w in args.window],
            tables=args.tables,
            database=connection.get_dsn_parameters().get("dbname")
            if hasattr(connection, "get_dsn_parameters") else None,
            audit_periods=not args.window_only,
            running_version=running_version,
            deployment_target=deployment_target,
        )
    finally:
        connection.rollback()
        connection.close()
    if args.json_stdout or not args.out:
        sys.stdout.write(to_json(report) + "\n")
    if args.out:
        json_path, md_path = write_report(report, args.out)
        sys.stderr.write(f"Wrote {json_path} and {md_path}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
