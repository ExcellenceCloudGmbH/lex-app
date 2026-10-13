"""The usage recorder's storage and its process-local front.

Two raw-SQL tables in the instance's own database (``lex/core/sql/usage_metrics.sql``):

* ``lex_usage_daily``: one row per person or API key, product area, channel and UTC day;
* ``lex_runtime_versions``: one row per lex-app version this instance has served.

The channel says who saw the use:

* ``api`` is written by the middleware, for API activity;
* ``ui`` and ``ui_embed`` are written by ``POST api/usage/area``, for the web interface's
  area events, the latter when the interface runs embedded.

The lex-analytics collector reads counts from both tables once a day (fleet metrics,
phase 1). Nothing here can change a request's outcome:

* :meth:`UsageRecorder.record` never raises;
* a database error pauses recording for a few minutes instead of failing every request;
* a person costs one insert per area, channel and day per process;
* a request nobody is attributed to writes nothing at all -- not a presence row, not
  the version row -- so an anonymous liveness probe never touches the database.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3, §6)
"""

from __future__ import annotations

import logging
import os
import threading
from datetime import date, datetime, timedelta, timezone as dt_timezone
from typing import Any, Callable, Optional

from django.db import connection, transaction

from lex._version import __version__

logger = logging.getLogger(__name__)

USAGE_TABLE = "lex_usage_daily"
VERSIONS_TABLE = "lex_runtime_versions"

USAGE_METRICS_ENV = "LEX_USAGE_METRICS"
_TRUE_VALUES = frozenset({"true", "1", "yes", "on"})
_FALSE_VALUES = frozenset({"false", "0", "no", "off"})

# Guards the "unrecognised value" warning below it to at most once per process --
# not time-based like WARNING_INTERVAL, since a misconfigured value does not change
# from one call to the next.
_invalid_value_lock = threading.Lock()
_warned_invalid_value = False

PERSON = "person"
API_KEY = "api_key"

CHANNEL_API = "api"
CHANNEL_UI = "ui"
CHANNEL_UI_EMBED = "ui_embed"

#: A process remembers at most this many presence keys before starting over.
MAX_SEEN = 100_000
#: After a database error the recorder stays quiet this long instead of retrying per request.
PAUSE_AFTER_ERROR = timedelta(minutes=5)
#: The version row is refreshed at most this often per process.
VERSION_INTERVAL = timedelta(hours=1)
#: A failing recorder warns at most this often per process.
WARNING_INTERVAL = timedelta(hours=1)


def _utc_now() -> datetime:
    return datetime.now(dt_timezone.utc)


def utc_day(now: Optional[datetime] = None) -> date:
    """The UTC calendar day of an aware instant (default: now).

    Never ``django.utils.timezone.localdate()``: that is the configured zone, Berlin by
    default, which would file a request made at 00:30 in Berlin under the next UTC day.
    """
    moment = now if now is not None else _utc_now()
    if moment.tzinfo is None:
        raise ValueError("utc_day() needs an aware datetime; a naive one has no day")
    return moment.astimezone(dt_timezone.utc).date()


def usage_metrics_enabled() -> bool:
    """Whether usage metrics recording is on. Read on every call; fails closed.

    Unset or empty is on, and so are ``true``, ``1``, ``yes`` and ``on``; ``false``,
    ``0``, ``no`` and ``off`` are off. Matching is case-insensitive and surrounding
    whitespace is ignored. Any other value is off too -- a typo must never silently
    turn recording back on -- and logs one warning naming the setting, at most once
    per process so a persistently bad value cannot flood the log.
    """
    global _warned_invalid_value

    raw = os.getenv(USAGE_METRICS_ENV, "")
    value = raw.strip().lower()
    if not value or value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False

    with _invalid_value_lock:
        should_warn = not _warned_invalid_value
        _warned_invalid_value = True
    if should_warn:
        logger.warning(
            "%s=%r is not a recognised value; usage metrics recording is off.",
            USAGE_METRICS_ENV, raw,
        )
    return False


def supported() -> bool:
    """The tables exist only on PostgreSQL; the migration is a no-op elsewhere."""
    return connection.vendor == "postgresql"


# ── storage ──────────────────────────────────────────────────────────────────


def insert_presence(day: date, actor_kind: str, actor_id: str, area: str, channel: str) -> None:
    """Insert one presence row; a row that already exists is left alone."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {USAGE_TABLE} (day, actor_kind, actor_id, area, channel) "
            f"VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            [day, actor_kind, actor_id, area, channel],
        )


def upsert_runtime_version(version: str, usage_enabled: bool) -> None:
    """Record that ``version`` is serving now, and whether usage is being recorded."""
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {VERSIONS_TABLE} (version, usage_enabled) VALUES (%s, %s) "
            f"ON CONFLICT (version) DO UPDATE "
            f"SET last_seen = now(), usage_enabled = EXCLUDED.usage_enabled",
            [version, usage_enabled],
        )


def _rows(cursor) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def presence_rows() -> list[dict[str, Any]]:
    """Every presence row, ordered. For tests and operators."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT day, actor_kind, actor_id, area, channel FROM {USAGE_TABLE} "
            f"ORDER BY day, actor_kind, actor_id, area, channel"
        )
        return _rows(cursor)


def runtime_versions() -> list[dict[str, Any]]:
    """Every version row, oldest first. For tests and operators."""
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT version, first_seen, last_seen, usage_enabled FROM {VERSIONS_TABLE} "
            f"ORDER BY first_seen"
        )
        return _rows(cursor)


def clear_all() -> None:
    """Empty both tables. Tests only: they are not models, so the test flush never does."""
    with connection.cursor() as cursor:
        cursor.execute(f"DELETE FROM {USAGE_TABLE}")
        cursor.execute(f"DELETE FROM {VERSIONS_TABLE}")


# ── the process-local front ─────────────────────────────────────────────────


class UsageRecorder:
    """Deduplicates, throttles and isolates the writes one process makes."""

    def __init__(self, clock: Callable[[], datetime] = _utc_now) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._seen: set[tuple[date, str, str, str, str]] = set()
        self._seen_day: Optional[date] = None
        self._paused_until: Optional[datetime] = None
        self._version_due_at: Optional[datetime] = None
        self._warned_at: Optional[datetime] = None

    def reset(self) -> None:
        """Forget everything this process remembered. Tests only."""
        with self._lock:
            self._seen.clear()
            self._seen_day = None
            self._paused_until = None
            self._version_due_at = None
            self._warned_at = None

    def record(
        self,
        actor: Optional[tuple[str, str]],
        area: Optional[str],
        channel: str = CHANNEL_API,
    ) -> None:
        """Record that ``actor`` used ``area`` on ``channel``. Never raises.

        ``actor`` is ``(kind, id)``, or None for a request nobody is attributed to --
        an anonymous caller writes nothing at all, not a presence row and not the
        version row, and does not disturb the version row's hourly throttle either.
        ``area`` is None for a request that is not recorded; with an actor present the
        version row is still refreshed when it is due.
        """
        if not supported():
            return
        now = self._clock()
        try:
            enabled = usage_metrics_enabled()
            write_version, key = self._plan(now, actor, area, channel, enabled)
            if write_version:
                upsert_runtime_version(__version__, enabled)
            if key is not None:
                insert_presence(*key)
                with self._lock:
                    self._seen.add(key)
        except Exception:  # recording must never change a response
            self._pause(now)

    def _plan(self, now: datetime, actor, area, channel: str, enabled: bool):
        with self._lock:
            # A request nobody is attributed to writes nothing: not a presence row,
            # not the version row. Returning before the version decision means an
            # anonymous request neither consumes nor resets the hourly throttle --
            # the next authenticated request still finds it due exactly when it would
            # have been had the anonymous request never happened.
            if actor is None:
                return False, None
            if self._paused_until is not None and now < self._paused_until:
                return False, None
            write_version = self._version_due_at is None or now >= self._version_due_at
            if write_version:
                self._version_due_at = now + VERSION_INTERVAL
            if not enabled or area is None:
                return write_version, None
            day = utc_day(now)
            if day != self._seen_day or len(self._seen) >= MAX_SEEN:
                self._seen.clear()
                self._seen_day = day
            key = (day, actor[0], actor[1], area, channel)
            return write_version, (None if key in self._seen else key)

    def _pause(self, now: datetime) -> None:
        with self._lock:
            self._paused_until = now + PAUSE_AFTER_ERROR
            self._version_due_at = None
            warn = self._warned_at is None or now - self._warned_at >= WARNING_INTERVAL
            if warn:
                self._warned_at = now
        if warn:
            logger.warning(
                "Usage metrics could not be recorded; recording pauses for %s. "
                "The response was not affected.",
                PAUSE_AFTER_ERROR,
                exc_info=True,
            )


#: The one recorder per process, used by the middleware and the usage-area endpoint.
recorder = UsageRecorder()
