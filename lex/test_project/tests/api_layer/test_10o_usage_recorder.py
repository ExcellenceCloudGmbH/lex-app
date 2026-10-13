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

import os
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.db import DatabaseError, IntegrityError, connection, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import URLPattern, URLResolver, get_resolver
from rest_framework import status

import pytest

from lex._version import __version__
from lex.core.services import usage_metrics
from lex.core.services.usage_areas import API_AREAS
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, API_SIMPLE, ApiSimpleItem

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


class TestCluster10o_Rules(SimpleTestCase):
    """Cluster 10o: the rules the recorder runs by: UTC days, and last place in MIDDLEWARE."""

    # -- 10.87 ---------------------------------------------------------
    def test_10_87_the_day_is_utc_even_when_berlin_has_moved_on(self) -> None:
        """
        Scenario 10.87: a request at 00:30 Berlin on 26 Oct is filed under 25 Oct.
        Given: 2026-10-25T23:30Z, the evening summer time ended, which is 00:30 on
               26 Oct in Berlin; and 00:30Z the same day, inside the hour Berlin repeats.
        When: its day is computed.
        Then: both are 2026-10-25, whether the instant is given in UTC or in Berlin time,
              and a naive datetime is refused rather than guessed.
        """
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 25, 23, 30, tzinfo=timezone.utc)),
            date(2026, 10, 25),
        )
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 26, 0, 30, tzinfo=ZoneInfo("Europe/Berlin"))),
            date(2026, 10, 25),
            "The same instant expressed in Berlin time must land on the same UTC day.",
        )
        self.assertEqual(
            usage_metrics.utc_day(datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)),
            date(2026, 10, 25),
        )
        with self.assertRaises(ValueError):
            usage_metrics.utc_day(datetime(2026, 10, 25, 23, 30))

    # -- 10.88 ---------------------------------------------------------
    def test_10_88_the_middleware_is_the_last_one(self) -> None:
        """
        Scenario 10.88: the recorder sees the user every other middleware settled on.
        Given: the project settings.
        When: MIDDLEWARE is read.
        Then: the usage recorder is its last entry.
        """
        from django.conf import settings

        self.assertEqual(
            settings.MIDDLEWARE[-1],
            "lex.core.middleware.usage_recorder.UsageRecorderMiddleware",
            "The recorder must come after every authentication middleware.",
        )


class _Clock:
    """A settable clock for UsageRecorder."""

    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def _keys(rows) -> list[tuple]:
    return [(r["actor_kind"], r["actor_id"], r["area"], r["channel"]) for r in rows]


class TestCluster10o_Recorder(TestCase):
    """Cluster 10o: storage and the process-local front, against the real tables."""

    PERSON_7 = (usage_metrics.PERSON, "7")

    def setUp(self) -> None:
        super().setUp()
        _skip_unless_postgresql(self)
        # The usage tables are raw SQL, outside the model registry, so the per-test
        # flush never clears them. Now that UsageRecorderMiddleware is installed,
        # every other E2E test in this cluster that makes a real request also writes
        # real rows here -- start each test from an empty table rather than asserting
        # on whatever an unrelated test left behind.
        usage_metrics.clear_all()

    # -- 10.89 ---------------------------------------------------------
    def test_10_89_a_presence_row_is_written_once(self) -> None:
        """
        Scenario 10.89: inserting the same presence twice leaves one row.
        Given: an empty usage table.
        When: the same (day, actor, area, channel) is inserted twice.
        Then: one row exists.
        """
        usage_metrics.insert_presence(date(2026, 10, 1), "person", "7", "grid", "api")
        usage_metrics.insert_presence(date(2026, 10, 1), "person", "7", "grid", "api")

        self.assertEqual(
            usage_metrics.presence_rows(),
            [{"day": date(2026, 10, 1), "actor_kind": "person", "actor_id": "7",
              "area": "grid", "channel": "api"}],
        )

    # -- 10.90 ---------------------------------------------------------
    def test_10_90_the_version_row_is_upserted(self) -> None:
        """
        Scenario 10.90: one row per version, carrying the latest switch state.
        Given: a version recorded with usage on.
        When: it is recorded again with usage off.
        Then: one row, usage_enabled false, last_seen not before first_seen.
        """
        usage_metrics.upsert_runtime_version("9.9.9", True)
        usage_metrics.upsert_runtime_version("9.9.9", False)

        rows = usage_metrics.runtime_versions()
        self.assertEqual(len(rows), 1, rows)
        self.assertEqual(rows[0]["version"], "9.9.9")
        self.assertFalse(rows[0]["usage_enabled"])
        self.assertGreaterEqual(rows[0]["last_seen"], rows[0]["first_seen"])

    # -- 10.91 ---------------------------------------------------------
    def test_10_91_the_recorder_writes_a_person_once_per_area_and_day(self) -> None:
        """
        Scenario 10.91: a busy person costs one insert per area per day.
        Given: a recorder with a fixed clock.
        When: the same person uses the grid twice and a record once, through the API.
        Then: one insert reaches the database for the grid, one for the record.
        """
        recorder = usage_metrics.UsageRecorder(clock=_Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc)))
        with patch.object(usage_metrics, "insert_presence", wraps=usage_metrics.insert_presence) as spy:
            recorder.record(self.PERSON_7, "grid")
            recorder.record(self.PERSON_7, "grid")
            recorder.record(self.PERSON_7, "record")

        self.assertEqual(spy.call_count, 2, f"Expected one insert per area, got {spy.call_args_list}")
        self.assertEqual({row["area"] for row in usage_metrics.presence_rows()}, {"grid", "record"})

    # -- 10.92 ---------------------------------------------------------
    def test_10_92_a_new_utc_day_starts_at_utc_midnight(self) -> None:
        """
        Scenario 10.92: the same request either side of UTC midnight is two days.
        Given: a person at 2026-10-25T23:59:59Z and again at 2026-10-26T00:00:00Z.
        When: both are recorded.
        Then: two rows, filed under 25 and 26 October.
        """
        clock = _Clock(datetime(2026, 10, 25, 23, 59, 59, tzinfo=timezone.utc))
        recorder = usage_metrics.UsageRecorder(clock=clock)
        recorder.record(self.PERSON_7, "grid")
        clock.now = datetime(2026, 10, 26, 0, 0, 0, tzinfo=timezone.utc)
        recorder.record(self.PERSON_7, "grid")

        self.assertEqual(
            [row["day"] for row in usage_metrics.presence_rows()],
            [date(2026, 10, 25), date(2026, 10, 26)],
        )

    # -- 10.93 ---------------------------------------------------------
    def test_10_93_a_database_error_pauses_recording_and_never_raises(self) -> None:
        """
        Scenario 10.93: a failing table costs one warning, not every request.
        Given: inserts that raise DatabaseError.
        When: two requests are recorded within the pause, and one after it.
        Then: nothing raises; the second request does not touch the database; the third
              retries; one warning is logged in the hour.
        """
        clock = _Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
        recorder = usage_metrics.UsageRecorder(clock=clock)
        with patch.object(usage_metrics, "insert_presence", side_effect=DatabaseError("simulated")) as failing, \
                self.assertLogs("lex.core.services.usage_metrics", level="WARNING") as logs:
            recorder.record(self.PERSON_7, "grid")
            clock.now += timedelta(minutes=1)
            recorder.record(self.PERSON_7, "record")
            clock.now += usage_metrics.PAUSE_AFTER_ERROR
            recorder.record(self.PERSON_7, "export")

        self.assertEqual(failing.call_count, 2, "Inside the pause the recorder must not retry")
        self.assertEqual(len(logs.records), 1, "At most one warning per hour")
        self.assertIn("could not be recorded", logs.output[0])

    # -- 10.105 --------------------------------------------------------
    def test_10_105_the_same_area_on_two_channels_is_two_rows(self) -> None:
        """
        Scenario 10.105: API activity and interface use are kept apart.
        Given: a recorder with a fixed clock.
        When: one person reaches the grid through the API, opens it in the interface, and
              opens it embedded.
        Then: three rows, one per channel. K2 reads ui and ui_embed and never adds api to
              them (spec §5.3.2).
        """
        recorder = usage_metrics.UsageRecorder(clock=_Clock(datetime(2026, 10, 1, 9, tzinfo=timezone.utc)))
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_API)
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_UI)
        recorder.record(self.PERSON_7, "grid", usage_metrics.CHANNEL_UI_EMBED)

        self.assertEqual(
            _keys(usage_metrics.presence_rows()),
            [("person", "7", "grid", "api"), ("person", "7", "grid", "ui"), ("person", "7", "grid", "ui_embed")],
        )


def _url_names(patterns) -> set[str]:
    names: set[str] = set()
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            names |= _url_names(pattern.url_patterns)
        elif isinstance(pattern, URLPattern) and pattern.name:
            names.add(pattern.name)
    return names


class TestCluster10o_RecorderEndToEnd(E2ETestCase):
    """Cluster 10o: real API requests through the whole middleware stack leave api rows."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()

    def tearDown(self) -> None:
        usage_metrics.clear_all()
        usage_metrics.recorder.reset()
        super().tearDown()

    def _get(self, url: str):
        before = usage_metrics.utc_day()
        response = self.client.get(url)
        after = usage_metrics.utc_day()
        return response, {before, after}

    def _assert_ok(self, response) -> None:
        self.assertEqual(
            response.status_code, status.HTTP_200_OK,
            f"The request itself must succeed for this scenario: {response.status_code} {response.content[:300]!r}",
        )

    # -- 10.94 ---------------------------------------------------------
    def test_10_94_a_person_listing_records_the_grid(self) -> None:
        """
        Scenario 10.94: a person listing a model is active in the grid today, on the API.
        Given: a signed-in person.
        When: they GET a model's list endpoint.
        Then: one presence row: their user id, area ``grid``, channel ``api``, today's
              UTC day.
        """
        response, days = self._get(self.url_list(API_SIMPLE))
        self._assert_ok(response)

        rows = usage_metrics.presence_rows()
        self.assertEqual(len(rows), 1, rows)
        self.assertIn(rows[0]["day"], days)
        self.assertEqual(_keys(rows), [("person", str(self.user.pk), "grid", "api")])

    # -- 10.95 ---------------------------------------------------------
    def test_10_95_a_repeated_request_leaves_one_row(self) -> None:
        """
        Scenario 10.95: presence, not volume.
        Given: a signed-in person.
        When: they GET the same list three times.
        Then: one presence row.
        """
        for _ in range(3):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(len(usage_metrics.presence_rows()), 1)

    # -- 10.96 ---------------------------------------------------------
    def test_10_96_history_is_recorded_from_both_of_its_routes(self) -> None:
        """
        Scenario 10.96: the history tab and the grid's as-of mode are history.
        Given: a record with history.
        When: the person opens its history, and the list with ``as_of``.
        Then: a ``history`` row on ``api``, and no ``grid`` row.
        """
        item = ApiSimpleItem.objects.create(name="h", value=1)
        self._assert_ok(self._get(self.url_history(API_SIMPLE, item.pk))[0])
        self._assert_ok(self._get(f"{self.url_list(API_SIMPLE)}?as_of=2030-01-01T00:00:00Z")[0])

        self.assertEqual(
            {(row["area"], row["channel"]) for row in usage_metrics.presence_rows()},
            {("history", "api")},
        )

    # -- 10.97 ---------------------------------------------------------
    def test_10_97_an_api_key_is_recorded_by_its_name_on_the_api_channel(self) -> None:
        """
        Scenario 10.97: an API key is an actor of its own, never a person.
        Given: a caller authenticated with an API key named "Nightly import".
        When: it GETs a list.
        Then: one row, actor ``api_key`` / "Nightly import", area ``grid``, channel
              ``api``, the only channel an API key appears on (spec §5.3.2).
        """
        self.authenticate_as_api_key(name="Nightly import")
        self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(
            _keys(usage_metrics.presence_rows()),
            [("api_key", "Nightly import", "grid", "api")],
        )

    # -- 10.98 ---------------------------------------------------------
    def test_10_98_the_instance_platform_key_is_never_recorded(self) -> None:
        """
        Scenario 10.98: the Instance Controller's own calls are not usage.
        Given: a caller whose key is the instance's LEX_API_KEY.
        When: it GETs a list.
        Then: no presence row.
        """
        raw_key = self.authenticate_as_api_key(name="instance")
        with patch.dict(os.environ, {"LEX_API_KEY": raw_key}):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.99 ---------------------------------------------------------
    def test_10_99_a_failed_request_is_not_usage(self) -> None:
        """
        Scenario 10.99: only successful requests count.
        Given: a signed-in person.
        When: they GET a record that does not exist (404).
        Then: no presence row.
        """
        response, _ = self._get(self.url_detail(API_SIMPLE, 987654))
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

        self.assertEqual(usage_metrics.presence_rows(), [])

    # -- 10.100 --------------------------------------------------------
    def test_10_100_switched_off_records_the_version_but_no_usage(self) -> None:
        """
        Scenario 10.100: LEX_USAGE_METRICS=false stops usage rows and says so.
        Given: the switch set to false.
        When: a person GETs a list.
        Then: no presence row; one version row with usage_enabled false, so the collector
              reports "disabled" rather than "nobody used it".
        """
        with patch.dict(os.environ, {"LEX_USAGE_METRICS": "false"}):
            self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        self.assertEqual(usage_metrics.presence_rows(), [])
        versions = usage_metrics.runtime_versions()
        self.assertEqual([(v["version"], v["usage_enabled"]) for v in versions], [(__version__, False)])

    # -- 10.101 --------------------------------------------------------
    def test_10_101_the_running_version_is_recorded(self) -> None:
        """
        Scenario 10.101: a served request records the version serving it.
        Given: the switch at its default.
        When: a person GETs a list.
        Then: one version row: the running lex-app version, usage enabled.
        """
        self._assert_ok(self._get(self.url_list(API_SIMPLE))[0])

        versions = usage_metrics.runtime_versions()
        self.assertEqual([(v["version"], v["usage_enabled"]) for v in versions], [(__version__, True)])

    # -- 10.102 --------------------------------------------------------
    def test_10_102_a_storage_failure_never_changes_the_response(self) -> None:
        """
        Scenario 10.102: the recorder failing is invisible to the person.
        Given: presence inserts that raise DatabaseError.
        When: a person GETs a list.
        Then: HTTP 200 with the normal body, and one warning.
        """
        ApiSimpleItem.objects.create(name="visible", value=1)
        with patch.object(usage_metrics, "insert_presence", side_effect=DatabaseError("simulated")), \
                self.assertLogs("lex.core.services.usage_metrics", level="WARNING") as logs:
            response, _ = self._get(self.url_list(API_SIMPLE))

        self._assert_ok(response)
        self.assertIn("visible", response.content.decode(), "The list body must be unaffected")
        self.assertEqual(len(logs.records), 1)

    # -- 10.103 --------------------------------------------------------
    def test_10_103_every_mapped_name_is_a_real_route(self) -> None:
        """
        Scenario 10.103: the area map cannot silently rot.
        Given: the live URLconf.
        When: its route names are collected.
        Then: every name in the API area map is among them; a renamed route would
              otherwise fall into ``app`` and its area would read zero forever.
        """
        missing = set(API_AREAS) - _url_names(get_resolver().url_patterns)
        self.assertEqual(missing, set(), f"Area map names with no route: {sorted(missing)}")
