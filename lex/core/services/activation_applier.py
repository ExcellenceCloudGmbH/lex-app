"""
lex-app's side of the in-database activation applier.

The applier itself is SQL (``lex/core/sql/bitemporal_activation.sql``), installed by the
``lex.core`` migration and called every minute by a pg_cron job the instance controller
registers. Nothing in this module runs at apply time. What lives here:

* :func:`applier_is_alive` — the **write-time liveness gate**. A future-dated save asks
  whether the database applied something recently. If yes, the save leaves only the
  ``SCHEDULED`` meta row: the database will activate it. If no — SQLite, a PostgreSQL
  without pg_cron, an instance whose job is not registered yet, a migration that has
  not run — the save also arms the in-process timer it always has. The decision is
  made from a heartbeat the applier writes on every run, so no configuration can
  claim an applier that is not there, and the lex-app release is safe to deploy in
  any order relative to the infrastructure change.

* Thin invokers for tests and operators (:func:`apply_due_activations`,
  :func:`pending_activations`, :func:`bitemporal_tables`, :func:`install_functions`).
  They issue one ``SELECT`` each; they add no logic.

Design: docs/superpowers/specs/2026-09-16-bitemporal-activation-applier-design.md (§5.8).
"""

from __future__ import annotations

import logging
import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from django.db import DatabaseError, connection as default_connection, transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

SQL_PATH = Path(__file__).resolve().parents[1] / "sql" / "bitemporal_activation.sql"

HEARTBEAT_TABLE = "lex_activation_applier_state"

FUNCTION_NAMES = (
    "lex_bitemporal_tables",
    "lex_pending_activations",
    "lex_apply_due_activations",
)

#: How recent the heartbeat must be for the database applier to count as alive.
#: pg_cron fires every minute; five minutes absorbs a slow tick or a failover without
#: arming redundant timers, while a dead applier is noticed within the same window.
DEFAULT_LIVENESS_WINDOW_SECONDS = 300
LIVENESS_WINDOW_ENV = "LEX_ACTIVATION_APPLIER_LIVENESS_SECONDS"


def liveness_window() -> timedelta:
    """The heartbeat freshness window, read from the environment on every call."""
    raw = os.getenv(LIVENESS_WINDOW_ENV, "")
    try:
        seconds = int(raw) if raw else DEFAULT_LIVENESS_WINDOW_SECONDS
    except ValueError:
        logger.warning("%s=%r is not an integer; using %s", LIVENESS_WINDOW_ENV, raw,
                       DEFAULT_LIVENESS_WINDOW_SECONDS)
        seconds = DEFAULT_LIVENESS_WINDOW_SECONDS
    return timedelta(seconds=max(seconds, 0))


def applier_is_alive(*, now=None, using=None) -> bool:
    """
    True when the database applier has run within :func:`liveness_window`.

    False on any non-PostgreSQL backend, when the heartbeat table is missing (the
    migration has not run), when it is empty (the applier has never run), or when the
    last run is older than the window. Never raises: the query runs in a savepoint so a
    missing table cannot poison the caller's transaction, and a database error simply
    means "not alive", which falls back to the behaviour lex-app has always had.
    """
    conn = default_connection if using is None else transaction.get_connection(using)
    if conn.vendor != "postgresql":
        return False
    try:
        with transaction.atomic(using=conn.alias), conn.cursor() as cursor:
            cursor.execute(f"SELECT last_run_at FROM {HEARTBEAT_TABLE} WHERE id = 1")
            row = cursor.fetchone()
    except DatabaseError as exc:  # table not migrated yet, connection trouble, ...
        logger.debug("Activation applier liveness unknown (%s); treating as not alive", exc)
        return False
    if row is None:
        return False
    now = now or timezone.now()
    return (now - row[0]) <= liveness_window()


def _rows(cursor) -> list[dict[str, Any]]:
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


# ── heartbeat access (operators and tests; the applier itself writes it in SQL) ──


def read_heartbeat(using=None) -> dict[str, Any] | None:
    """The heartbeat row as a dict, or None when the applier has never run."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute(
            f"SELECT last_run_at, last_run_applied, last_run_failed, last_run_duration_ms "
            f"FROM {HEARTBEAT_TABLE} WHERE id = 1"
        )
        rows = _rows(cursor)
    return rows[0] if rows else None


def record_heartbeat(*, last_run_at=None, applied: int = 0, failed: int = 0,
                     duration_ms: int = 0, using=None) -> None:
    """Write the heartbeat row as the applier would. Used to simulate a live applier."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute(
            f"INSERT INTO {HEARTBEAT_TABLE} (id, last_run_at, last_run_applied, last_run_failed, last_run_duration_ms) "
            f"VALUES (1, %s, %s, %s, %s) "
            f"ON CONFLICT (id) DO UPDATE SET last_run_at = EXCLUDED.last_run_at, "
            f"last_run_applied = EXCLUDED.last_run_applied, last_run_failed = EXCLUDED.last_run_failed, "
            f"last_run_duration_ms = EXCLUDED.last_run_duration_ms",
            [last_run_at or timezone.now(), applied, failed, duration_ms],
        )


def clear_heartbeat(using=None) -> None:
    """Remove the heartbeat row: the state before the applier has ever run."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute(f"DELETE FROM {HEARTBEAT_TABLE} WHERE id = 1")


# ── thin invokers ──────────────────────────────────────────────────────────────


def install_functions(using=None) -> None:
    """(Re)install the SQL functions from :data:`SQL_PATH`. PostgreSQL only."""
    conn = default_connection if using is None else transaction.get_connection(using)
    if conn.vendor != "postgresql":
        raise RuntimeError("The activation applier is PostgreSQL-only")
    with conn.cursor() as cursor:
        cursor.execute(SQL_PATH.read_text(encoding="utf-8"))


def apply_due_activations(using=None) -> int:
    """Run one applier tick. Returns the number of records converged."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute("SELECT lex_apply_due_activations()")
        return int(cursor.fetchone()[0])


def pending_activations(using=None) -> list[dict[str, Any]]:
    """Every SCHEDULED meta row across every bitemporal table, due or not."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM lex_pending_activations()")
        return _rows(cursor)


def bitemporal_tables(using=None) -> list[dict[str, Any]]:
    """The (main, history, meta, pk) triples the applier discovers right now."""
    conn = default_connection if using is None else transaction.get_connection(using)
    with conn.cursor() as cursor:
        cursor.execute("SELECT * FROM lex_bitemporal_tables()")
        return _rows(cursor)
