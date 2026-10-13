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
from django.test import SimpleTestCase, TestCase

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


class TestCluster10o_AreaMap(SimpleTestCase):
    """Cluster 10o: a request's URL name decides its API area; the interface has its list."""

    EXPECTED = {
        "model-entries-list": "grid",
        "model-history-list": "history",
        "model-one-entry-read-update-delete": "record",
        "model-one-entry-create": "record",
        "model-many-entries": "record",
        "model-export": "export",
        "calculationlog-tree": "calculation_logs",
        "init-calculation-logs": "calculation_logs",
        "download-markdown-pdf": "calculation_logs",
        "process": "processes",
        "run_step": "processes",
        "htmlreports": "reports",
        "streamlit_token": "analytics",
        # Not in spec §5.3.1's table, but routed in lex-app-v2
        # (process_admin_site.py:234-238); kept on purpose, see the plan's Task 2.
        "global-search": "search",
        "file-download": "files",
        "sharepoint-file-download": "files",
        "sharepoint-file-share-link": "files",
        "sharepoint-file-preview-link": "files",
    }

    # Spec §5.3.2, "What counts as use": the interface's areas, name for name.
    SPEC_UI_AREAS = {
        "grid", "record", "create", "history", "audit_log", "calculate", "calculation_logs",
        "processes", "reports", "analytics", "dashboard", "search", "export", "files",
        "monitoring",
    }

    # -- 10.84 ---------------------------------------------------------
    def test_10_84_every_mapped_route_lands_in_its_area(self) -> None:
        """
        Scenario 10.84: the API area map matches the spec's table.
        Given: each URL name the spec assigns to an area.
        When: it is classified without query parameters.
        Then: it lands in that area, and the map holds exactly those names.
        """
        from lex.core.services.usage_areas import API_AREAS, area_for

        self.assertEqual(
            dict(API_AREAS), self.EXPECTED,
            "The API area map drifted from spec §5.3.1; the api channel would count the wrong areas.",
        )
        for url_name, area in self.EXPECTED.items():
            with self.subTest(url_name=url_name):
                self.assertEqual(area_for(url_name, {}), area, f"{url_name} must count as {area}")

    # -- 10.85 ---------------------------------------------------------
    def test_10_85_as_of_turns_a_list_into_history(self) -> None:
        """
        Scenario 10.85: the grid in as-of mode is history.
        Given: a list request carrying ``as_of``.
        When: it is classified.
        Then: it counts as ``history``; the same request without ``as_of`` is ``grid``,
              and ``as_of`` on any other route changes nothing.
        """
        from lex.core.services.usage_areas import area_for

        self.assertEqual(area_for("model-entries-list", {"as_of": "2026-09-01T00:00:00Z"}), "history")
        self.assertEqual(area_for("model-entries-list", {"as_of": ""}), "grid")
        self.assertEqual(area_for("model-export", {"as_of": "2026-09-01T00:00:00Z"}), "export")

    # -- 10.86 ---------------------------------------------------------
    def test_10_86_unmapped_routes_are_the_app_and_unnamed_ones_are_not_recorded(self) -> None:
        """
        Scenario 10.86: a named route nobody mapped still says "this person was here".
        Given: an unmapped URL name, a route with no name, and the usage endpoint's name.
        When: they are classified.
        Then: the unmapped name is ``app``. The unnamed route is ``None``: the SPA's
              catch-all and static assets are not use (spec §5.3.1). The usage endpoint
              is ``None`` too: its event is recorded on its interface channel, and a
              second ``api`` row for the same request would count it twice.
        """
        from lex.core.services.usage_areas import APP, API_AREAS, USAGE_AREA_URL_NAME, area_for

        self.assertEqual(area_for("model-structure", {}), APP)
        self.assertIsNone(area_for(None, {}))
        self.assertIsNone(area_for("", {}))
        self.assertIsNone(area_for(USAGE_AREA_URL_NAME, {}))
        self.assertNotIn(USAGE_AREA_URL_NAME, API_AREAS)

    # -- 10.104 --------------------------------------------------------
    def test_10_104_the_interface_list_is_the_spec_list_and_contains_every_api_area_but_app(self) -> None:
        """
        Scenario 10.104: the endpoint accepts exactly the spec's interface areas.
        Given: ``UI_AREAS`` and ``API_AREAS``.
        When: they are compared with spec §5.3.2 and with each other.
        Then: ``UI_AREAS`` is the spec's list, name for name; every API area except
              ``app`` is in it; ``app`` is not, because it is the catch-all for "some
              request", not a product area a person chooses.
        """
        from lex.core.services.usage_areas import APP, API_AREAS, UI_AREAS

        self.assertEqual(set(UI_AREAS), self.SPEC_UI_AREAS,
                         "The interface mirrors this list; change both in the same release pair.")
        self.assertLessEqual(set(API_AREAS.values()), set(UI_AREAS))
        self.assertNotIn(APP, UI_AREAS)
