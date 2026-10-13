"""The usage recorder: which product area each person used, per channel and UTC day.

Intent: fleet metrics (LEX-749) counts active people (K1) and feature reach (K2) per
instance. Everything it knows about reads comes from the rows written here, into the
instance's own database. There are two writers:

* the middleware writes API activity on the ``api`` channel;
* ``POST api/usage/area`` writes the web interface's area events on ``ui`` or ``ui_embed``.

K2 is read from the interface channels. The ``api`` channel is supporting detail.

A recorder that is registered but never reached looks exactly like an instance nobody
uses. So these tests drive real requests through the whole middleware stack and assert
rows, and they pin the UTC day the Instance Controller buckets by.
Cluster 10o — scenarios 10.83–10.114. Type: U, I and E.
Covers: lex/core/sql/usage_metrics.sql, lex/core/migrations/0002_usage_metrics.py,
lex/core/services/usage_areas.py, lex/core/services/usage_metrics.py,
lex/core/middleware/usage_recorder.py, lex/api/views/usage/UsageArea.py and the
``usage-area`` route in lex/process_admin/sites/process_admin_site.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10o_usage_recorder.py -v
"""

from __future__ import annotations

from django.db import IntegrityError, connection, transaction
from django.test import TestCase

import pytest

pytestmark = pytest.mark.api_layer

_SKIP_NOT_PG = "the usage recorder's tables are PostgreSQL-only (0002_usage_metrics no-ops elsewhere)"


def _skip_unless_postgresql(test_case) -> None:
    """Skip ``test_case`` unless the active connection is PostgreSQL.

    ``lex_usage_daily`` and ``lex_runtime_versions`` exist only where
    ``0002_usage_metrics`` actually installed them -- it no-ops on every other backend,
    including the ``local`` SQLite target (``DATABASE_DEPLOYMENT_TARGET=local``, see
    ``lex/lex_app/settings.py``). Every class in this file that touches those tables
    should call this from its own ``setUp`` so it skips cleanly there instead of
    hard-failing on a table that was never supposed to exist.
    """
    if connection.vendor != "postgresql":
        test_case.skipTest(_SKIP_NOT_PG)


def _columns(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
            [table],
        )
        return {row[0] for row in cursor.fetchall()}


def _primary_key(table: str) -> set[str]:
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT kcu.column_name
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
              ON kcu.constraint_name = tc.constraint_name
             AND kcu.table_name = tc.table_name
            WHERE tc.constraint_type = 'PRIMARY KEY' AND tc.table_name = %s
            """,
            [table],
        )
        return {row[0] for row in cursor.fetchall()}


def _raw_insert(actor_kind: str, channel: str) -> None:
    with transaction.atomic(), connection.cursor() as cursor:
        cursor.execute(
            "INSERT INTO lex_usage_daily (day, actor_kind, actor_id, area, channel) "
            "VALUES ('2026-10-01', %s, '7', 'grid', %s)",
            [actor_kind, channel],
        )


class TestCluster10o_UsageTables(TestCase):
    """Cluster 10o: the recorder's tables exist, in the shape the collector reads."""

    def setUp(self) -> None:
        super().setUp()
        _skip_unless_postgresql(self)

    # -- 10.83 ---------------------------------------------------------
    def test_10_83_migration_installs_both_tables_with_their_keys(self) -> None:
        """
        Scenario 10.83: the core migration installs the usage tables.
        Given: a database migrated to the latest ``core`` migration.
        When: the catalog is read, and rows outside the channel rules are inserted.
        Then: ``lex_usage_daily`` carries day, actor_kind, actor_id, area, channel and
              first_seen, keyed by the first five; ``lex_runtime_versions`` carries
              version, first_seen, last_seen and usage_enabled, keyed by version; an
              unknown channel and an API key on an interface channel are refused.
        """
        self.assertEqual(
            _columns("lex_usage_daily"),
            {"day", "actor_kind", "actor_id", "area", "channel", "first_seen"},
            "lex_usage_daily drifted from the shape the Instance Controller queries.",
        )
        self.assertEqual(
            _primary_key("lex_usage_daily"),
            {"day", "actor_kind", "actor_id", "area", "channel"},
            "The presence key must be (day, actor, area, channel): one row per person, area, "
            "channel and day, so interface use and API activity are never merged.",
        )
        self.assertEqual(
            _columns("lex_runtime_versions"),
            {"version", "first_seen", "last_seen", "usage_enabled"},
            "lex_runtime_versions drifted from the shape the Instance Controller queries.",
        )
        self.assertEqual(
            _primary_key("lex_runtime_versions"),
            {"version"},
            "One row per lex-app version: first_seen per version is the upgrade date.",
        )
        with self.assertRaises(IntegrityError, msg="channel must be api, ui or ui_embed"):
            _raw_insert("person", "web")
        with self.assertRaises(IntegrityError, msg="an API key appears on the api channel only"):
            _raw_insert("api_key", "ui")
