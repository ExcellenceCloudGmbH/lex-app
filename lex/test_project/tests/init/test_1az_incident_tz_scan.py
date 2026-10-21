"""``lex incident_tz scan`` — the read-only scan for the TIME_ZONE incident.

Intent: before anything is corrected, the remediation plan (Q1) needs, for each
database, the periods in which affected code (rc212 – v2.1.3) wrote to it and a
verdict for every datetime value: A written by a clock, B a date stored at
midnight UTC by affected code, C a time of day written by affected code, D
correct, E not settled by the evidence. A wrong verdict either leaves damage in
place or, worse, sends a correct value to be "corrected" — so these scenarios
pin how each verdict follows from the database's own evidence (audit log,
history, meta-history) and that the scan never writes.

Cluster 1az — scenarios 1.420–1.435. Type: U + E.
Covers: lex/lex_app/incident_tz/scan.py, lex/lex_app/management/commands/incident_tz.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1az_incident_tz_scan.py -v
"""

from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
from io import StringIO

import pytest
from django.core.management import call_command
from django.db import connection, transaction
from django.test import SimpleTestCase

from lex.audit_logging.models.AuditLog import AuditLog
from lex.lex_app.incident_tz import scan as tzscan
from lex.lex_app.management.commands.incident_tz import declared_datetime_columns
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import SCAN_MODELS, IncidentScanItem

pytestmark = pytest.mark.init


def utc(*parts) -> dt.datetime:
    return dt.datetime(*parts, tzinfo=dt.timezone.utc)


def iso(value: dt.datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


# The instance's story as its audit log tells it (UTC).
LAST_PRE = utc(2026, 7, 1, 18, 38)          # last write by a pre-incident version
FIRST_AFFECTED = utc(2026, 7, 6, 11, 39)    # first write by rc221
LAST_AFFECTED = utc(2026, 8, 3, 15, 7)      # last write by v2.1.3
FIRST_FIXED = utc(2026, 8, 20, 16, 51)      # first write by v2.1.4

# Each update row's payload carries edited_at the way that version rendered it.
AUDIT_ROWS = [
    (LAST_PRE, "2026-07-01T20:38:00.123456"),            # Berlin digits, no offset
    (FIRST_AFFECTED, "2026-07-06T11:39:00.200000"),      # UTC digits, no offset
    (LAST_AFFECTED, "2026-08-03T15:07:00.300000Z"),      # UTC digits + "Z" (v2.1.1 – v2.1.3)
    (FIRST_FIXED, "2026-08-20T18:51:00.400000+02:00"),   # explicit offset (v2.1.4+)
]

BEFORE_RC212 = utc(2026, 6, 10, 10, 0)
START_GAP = utc(2026, 7, 3, 12, 0)          # after LAST_PRE, before FIRST_AFFECTED
IN_WINDOW = utc(2026, 7, 15, 10, 0)
AFTER_FIX = utc(2026, 9, 1, 10, 0)

# What affected code stored: the digits a Berlin user typed, read as UTC.
SUMMER_DATE_WRONG = utc(2026, 7, 20)        # the user picked 20 Jul
SUMMER_DATE_RIGHT = utc(2026, 7, 19, 22)    # 20 Jul 00:00 in Berlin (UTC+2)
WINTER_DATE_WRONG = utc(2026, 12, 31)
WINTER_DATE_RIGHT = utc(2026, 12, 30, 23)   # 31 Dec 00:00 in Berlin (UTC+1)
TIME_WRONG = utc(2026, 7, 21, 9, 30)        # the user typed 09:30
TIME_RIGHT = utc(2026, 7, 21, 7, 30)
DST_FALL_BACK = utc(2026, 10, 25, 2, 30)    # 02:30 happens twice in Berlin that night


class TestCluster01az_ScanDecisions(SimpleTestCase):
    """Cluster 1az: the decisions that need no database — eras, periods, positions."""

    def test_1_420_audit_era_reads_each_versions_format(self) -> None:
        """
        Scenario 1.420: an audit payload's edited_at tells which code rendered it.
        Given: an update row written at 10:00 UTC (12:00 in Berlin), in each format
        When: audit_era() reads the payload value next to the row's own date
        Then: Berlin digits → pre-incident; UTC digits, with or without "Z" →
              affected; an explicit non-zero offset → fixed; anything else → unknown
        """
        date_utc = dt.datetime(2026, 7, 15, 10, 0)
        date_local = dt.datetime(2026, 7, 15, 12, 0)
        cases = {
            "2026-07-15T12:00:00.123456": tzscan.ERA_PRE,
            "2026-07-15T10:00:00.123456": tzscan.ERA_AFFECTED,
            "2026-07-15T10:00:00.123456Z": tzscan.ERA_AFFECTED,
            "2026-07-15T12:00:00.123456+02:00": tzscan.ERA_FIXED,
            "2026-07-15T17:00:00": tzscan.ERA_UNKNOWN,
            "not a date": tzscan.ERA_UNKNOWN,
            None: tzscan.ERA_UNKNOWN,
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(
                    tzscan.audit_era(text, date_utc, date_local), expected,
                    f"{text!r} should read as {expected}.",
                )

    def test_1_421_affected_rows_become_one_period_with_two_brackets(self) -> None:
        """
        Scenario 1.421: a run of affected audit rows is one exposure period.
        Given: rows pre, unknown, affected, affected, fixed (oldest first)
        When: exposure_periods() reads them
        Then: one period that started after the pre row and by the first affected
              row, and ended after the last affected row and by the fixed row; the
              unknown row changes nothing
        """
        rows = [
            (LAST_PRE, tzscan.ERA_PRE),
            (utc(2026, 7, 4), tzscan.ERA_UNKNOWN),
            (FIRST_AFFECTED, tzscan.ERA_AFFECTED),
            (LAST_AFFECTED, tzscan.ERA_AFFECTED),
            (FIRST_FIXED, tzscan.ERA_FIXED),
        ]
        periods = tzscan.exposure_periods(rows)
        self.assertEqual(len(periods), 1, f"Expected one period, got {periods}.")
        period = periods[0]
        self.assertEqual(
            (period.started_after, period.started_by, period.ended_after, period.ended_by),
            (LAST_PRE, FIRST_AFFECTED, LAST_AFFECTED, FIRST_FIXED),
            "The brackets must come from the neighbouring audit rows.",
        )
        self.assertEqual(period.affected_rows, 2, "Both affected rows count.")

    def test_1_422_a_downgrade_opens_a_second_period_and_a_live_one_stays_open(self) -> None:
        """
        Scenario 1.422: every return to affected code is its own period.
        Given: affected, fixed, affected (a downgrade), fixed, affected (still running)
        When: exposure_periods() reads them
        Then: three periods; the first has no earlier row, the last has no later one
        """
        rows = [
            (utc(2026, 7, 10), tzscan.ERA_AFFECTED),
            (utc(2026, 7, 20), tzscan.ERA_FIXED),
            (utc(2026, 8, 14), tzscan.ERA_AFFECTED),
            (utc(2026, 9, 4), tzscan.ERA_FIXED),
            (utc(2026, 9, 10), tzscan.ERA_AFFECTED),
        ]
        periods = tzscan.exposure_periods(rows)
        self.assertEqual(len(periods), 3, f"Expected three periods, got {periods}.")
        self.assertIsNone(periods[0].started_after, "Nothing was written before the first.")
        self.assertEqual(periods[1].started_after, utc(2026, 7, 20))
        self.assertIsNone(periods[2].ended_by, "A period still running has no end.")

    def test_1_423_positions_respect_the_brackets_and_the_rc212_release(self) -> None:
        """
        Scenario 1.423: where a write falls relative to the exposure periods.
        Given: the period (LAST_PRE, FIRST_AFFECTED] … (LAST_AFFECTED, FIRST_FIXED]
        When: writes at different moments are placed
        Then: before rc212 was released → out; between the known affected writes →
              in; inside a bracket → edge; after the fix → out; a write known only to
              lie between a moment before and one inside → ambiguous; with no
              neighbouring rows, the edges run from the rc212 release and stay open
        """
        period = tzscan.Period(
            started_by=FIRST_AFFECTED, ended_after=LAST_AFFECTED,
            started_after=LAST_PRE, ended_by=FIRST_FIXED,
        )
        cases = [
            (BEFORE_RC212, tzscan.OUT),
            (IN_WINDOW, tzscan.IN),
            (START_GAP, tzscan.EDGE),
            (utc(2026, 8, 10), tzscan.EDGE),
            (AFTER_FIX, tzscan.OUT),
        ]
        for when, expected in cases:
            with self.subTest(when=when):
                self.assertEqual(tzscan.position_of(when, [period]), expected)
        self.assertEqual(tzscan.span_position(BEFORE_RC212, IN_WINDOW, [period]), tzscan.AMBIGUOUS)
        self.assertEqual(tzscan.span_position(FIRST_AFFECTED, LAST_AFFECTED, [period]), tzscan.IN)
        self.assertEqual(
            tzscan.span_position(BEFORE_RC212, utc(2026, 6, 20), [period]), tzscan.OUT,
            "A write wholly before the period is out.",
        )
        bare = tzscan.Period(started_by=FIRST_AFFECTED, ended_after=LAST_AFFECTED)
        self.assertEqual(tzscan.position_of(utc(2026, 6, 27), [bare]), tzscan.EDGE)
        self.assertEqual(tzscan.position_of(utc(2026, 6, 25), [bare]), tzscan.OUT)
        self.assertEqual(tzscan.position_of(AFTER_FIX, [bare]), tzscan.EDGE)
        self.assertEqual(
            tzscan.span_position(utc(2025, 3, 1), utc(2026, 6, 20), [bare]), tzscan.OUT,
            "A row created and last edited before rc212 was released cannot be affected, "
            "even when the audit log holds nothing older than the period.",
        )

    def test_1_424_run_on_its_own_with_the_instance_env_and_stated_windows(self) -> None:
        """
        Scenario 1.424: run on its own, the scan connects like the instance does.
        Given: the variables every instance pod carries, and a stated window
        When: connection_settings_from_env() and parse_window() read them
        Then: the instance's database settings (user defaults to django, port to
              5432); a missing variable or a backwards window is refused
        """
        env = {"DATABASE_NAME": "db_x", "POSTGRES_PASSWORD": "pw", "DATABASE_DOMAIN": "10.0.0.5"}
        self.assertEqual(
            tzscan.connection_settings_from_env(env),
            {"dbname": "db_x", "user": "django", "password": "pw", "host": "10.0.0.5", "port": 5432},
        )
        with self.assertRaises(tzscan.ScanError):
            tzscan.connection_settings_from_env({"DATABASE_NAME": "db_x"})
        self.assertEqual(
            tzscan.parse_window("2026-07-06T10:04+02:00/2026-10-21T09:00Z"),
            (utc(2026, 7, 6, 8, 4), utc(2026, 10, 21, 9, 0)),
            "Offsets are applied; the window is held in UTC.",
        )
        with self.assertRaises(tzscan.ScanError):
            tzscan.parse_window("2026-10-21T00:00Z/2026-07-06T00:00Z")


class TestCluster01az_ScanDatabase(E2ETestCase):
    """Cluster 1az: the scan against real lex tables — main, history, meta-history."""

    e2e_models = SCAN_MODELS

    def setUp(self) -> None:
        super().setUp()
        self.History = IncidentScanItem.history.model
        self.Meta = self.History.meta_history.model
        self.main = IncidentScanItem._meta.db_table
        self.history = self.History._meta.db_table
        self.meta = self.Meta._meta.db_table
        self.tables = [self.main, self.history, self.meta]

    # ── helpers ──────────────────────────────────────────────────────────

    def seed_audit(self, rows=AUDIT_ROWS) -> None:
        for when, edited_at in rows:
            log = AuditLog.objects.create(
                author="scan-test", resource="incidentscanitem", action="update",
                payload={"id": 1, "edited_at": edited_at},
            )
            AuditLog.objects.filter(pk=log.pk).update(date=when)

    def seed(self, versions, *, name="probe", created=None, edited=None) -> IncidentScanItem:
        """One record whose history holds ``versions``: ``[(recorded_at, fields)]``."""
        item = IncidentScanItem.objects.create(name=name, **versions[0][1])
        for _recorded, fields in versions[1:]:
            item = IncidentScanItem.objects.get(pk=item.pk)
            for key, value in fields.items():
                setattr(item, key, value)
            item.save()
        recorded = list(self.History.objects.filter(id=item.pk).order_by("history_id"))
        self.assertEqual(len(recorded), len(versions), "Seeding needs one version per save.")
        for row, (when, _fields) in zip(recorded, versions):
            self.History.objects.filter(history_id=row.history_id).update(valid_from=when)
            self.Meta.objects.filter(history_object_id=row.history_id).update(sys_from=when)
        IncidentScanItem.objects.filter(pk=item.pk).update(
            created_at=created or versions[0][0], edited_at=edited or versions[-1][0],
        )
        return item

    def make_raw_table(self) -> str:
        name = "lex_app_incidentscanraw"
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')
            cursor.execute(
                f'CREATE TABLE "{name}" (id serial PRIMARY KEY, due_on timestamptz, '
                "created_at timestamptz, edited_at timestamptz)"
            )
        self.addCleanup(self._drop_table, name)
        return name

    @staticmethod
    def _drop_table(name: str) -> None:
        with connection.cursor() as cursor:
            cursor.execute(f'DROP TABLE IF EXISTS "{name}"')

    def run_scan(self, **options) -> dict:
        options.setdefault("tables", self.tables)
        with transaction.atomic():
            with connection.cursor() as cursor:
                cursor.execute("SET TRANSACTION READ ONLY")
                return tzscan.scan(cursor, **options)

    @staticmethod
    def rows(report, table, column, row=None, record=None) -> list:
        return [
            r for r in report["rows"]
            if r["table"] == table and r["column"] == column
            and (row is None or r["row"] == str(row))
            and (record is None or r["record"] == str(record))
        ]

    def only(self, rows, what: str) -> dict:
        self.assertEqual(len(rows), 1, f"Expected exactly one {what}, got {rows}.")
        return rows[0]

    @staticmethod
    def column(report, table, column) -> dict:
        return next(c for c in report["columns"] if c["table"] == table and c["column"] == column)

    # ── scenarios ────────────────────────────────────────────────────────

    def test_1_425_refuses_to_run_outside_a_read_only_transaction(self) -> None:
        """
        Scenario 1.425: the scan cannot run where it could write.
        Given: a connection in an ordinary read-write transaction
        When: scan() is called
        Then: it refuses before reading anything
        """
        with connection.cursor() as cursor:
            with self.assertRaises(tzscan.ScanError, msg="A read-write session must be refused."):
                tzscan.scan(cursor, tables=self.tables)

    def test_1_426_the_window_comes_from_the_databases_own_audit_log(self) -> None:
        """
        Scenario 1.426: exposure is dated from the audit log, per database — and
        the report says so when the audit log cannot date it.
        Given: update rows from a pre-incident, two affected and a fixed version;
               then an audit log holding only rows whose edited_at is unreadable
        When: the scan runs on each
        Then: one period, (01 Jul 18:38, 06 Jul 11:39] to (03 Aug 15:07, 20 Aug 16:51],
              with every row in its era; for the unreadable log, no period and a
              report that asks for the period instead of claiming there was none
        """
        self.seed_audit([(AFTER_FIX, "None")])
        unreadable = self.run_scan()
        self.assertEqual(unreadable["exposure"]["periods"], [])
        self.assertEqual(unreadable["exposure"]["readable_rows"], 0)
        self.assertIn("cannot date any exposure", tzscan.render_markdown(unreadable))
        AuditLog.objects.all().delete()

        self.seed_audit()
        exposure = self.run_scan()["exposure"]
        self.assertEqual(exposure["source"], "audit_log")
        self.assertEqual(
            exposure["eras"], {"pre": 1, "affected": 2, "fixed": 1, "unknown": 0},
            "Each format must land in its era.",
        )
        self.assertEqual(
            exposure["periods"],
            [{
                "started_after": iso(LAST_PRE), "started_by": iso(FIRST_AFFECTED),
                "ended_after": iso(LAST_AFFECTED), "ended_by": iso(FIRST_FIXED),
                "affected_rows": 2, "source": "audit_log",
            }],
            "The period must be bracketed by the neighbouring audit rows.",
        )

    def test_1_427_census_covers_every_column_by_layer_and_reconciles_with_models(self) -> None:
        """
        Scenario 1.427: no datetime column can be missed, model or not.
        Given: the scan model's tables and a raw table no model declares
        When: the scan runs over the whole schema with the models' declared columns
        Then: main, history and meta-history columns carry their layers, the audit
              log is framework, and the raw table's columns are listed as
              undeclared while the model's are not
        """
        raw = self.make_raw_table()
        self.seed([(IN_WINDOW, {"due_on": SUMMER_DATE_WRONG})])
        report = self.run_scan(tables=None, expected_columns=declared_datetime_columns())
        layers = {(c["table"], c["column"]): c["layer"] for c in report["columns"]}
        expected = {
            (self.main, "due_on"): "main",
            (self.history, "due_on"): "history",
            (self.meta, "due_on"): "meta_history",
            ("audit_logging_auditlog", "date"): "framework",
            (raw, "due_on"): "main",
        }
        for key, layer in expected.items():
            with self.subTest(column=key):
                self.assertEqual(layers.get(key), layer, f"{key} should be in the {layer} layer.")
        recon = report["census"]["reconciliation"]
        self.assertIn(f"{raw}.due_on", recon["present_but_undeclared"])
        self.assertNotIn(f"{self.main}.due_on", recon["present_but_undeclared"])
        self.assertNotIn(f"{self.main}.due_on", recon["declared_but_missing"])

    def test_1_428_clock_written_values_are_A_even_at_midnight(self) -> None:
        """
        Scenario 1.428: a value with microseconds was written by a clock: correct.
        Given: inside the window, a due_on of 00:00:00.123456 UTC and a clock synced_at
        When: the scan runs
        Then: both are counted A and neither is listed for review
        """
        self.seed_audit()
        self.seed([(IN_WINDOW, {
            "due_on": utc(2026, 7, 20, 0, 0, 0, 123456),
            "synced_at": utc(2026, 7, 15, 10, 0, 5, 250000),
        })])
        report = self.run_scan()
        for column in ("due_on", "synced_at"):
            with self.subTest(column=column):
                counts = self.column(report, self.main, column)["verdicts"]
                self.assertEqual(counts, {"A": 1, "B": 0, "C": 0, "D": 0, "E": 0})
                self.assertEqual(self.rows(report, self.main, column), [])

    def test_1_429_a_date_stored_at_midnight_utc_inside_the_window_is_B_in_every_layer(self) -> None:
        """
        Scenario 1.429: the dominant damage, found in main, history and meta-history.
        Given: dates written inside the window at 00:00 UTC — one in July, one in December
        When: the scan runs
        Then: each is B in all three layers, corrected to Berlin midnight of the same
              date: 22:00 UTC the day before in summer, 23:00 in winter
        """
        self.seed_audit()
        summer = self.seed([(IN_WINDOW, {"due_on": SUMMER_DATE_WRONG})], name="summer")
        winter = self.seed([(IN_WINDOW, {"due_on": WINTER_DATE_WRONG})], name="winter")
        report = self.run_scan()
        for item, right in ((summer, SUMMER_DATE_RIGHT), (winter, WINTER_DATE_RIGHT)):
            history_id = self.History.objects.get(id=item.pk).history_id
            found = [
                ("main", self.only(self.rows(report, self.main, "due_on", row=item.pk), "main row")),
                ("history", self.only(self.rows(report, self.history, "due_on", record=item.pk), "history row")),
                ("meta", self.only(self.rows(report, self.meta, "due_on", record=history_id), "meta row")),
            ]
            for layer, row in found:
                with self.subTest(item=item.name, layer=layer):
                    self.assertEqual(row["verdict"], "B", f"{layer}: {row}")
                    self.assertEqual(row["corrected"], iso(right), f"{layer}: wrong correction.")
                    self.assertEqual(row["written"], iso(IN_WINDOW))

    def test_1_430_the_history_walk_dates_the_field_not_the_row(self) -> None:
        """
        Scenario 1.430: a value is as old as its final run of equal values.
        Given: two records with a correct date entered before rc212 and shifted by a
               save inside the window; after the fix one had another field edited,
               the other had its date put right by hand
        When: the scan runs
        Then: the first is B, dated by the in-window save although its row was last
              edited after the fix, and so are both history versions holding the
              shifted value; the second is not listed at all
        """
        self.seed_audit()
        carried = self.seed([
            (BEFORE_RC212, {"due_on": SUMMER_DATE_RIGHT}),
            (IN_WINDOW, {"due_on": SUMMER_DATE_WRONG}),
            (AFTER_FIX, {"name": "renamed"}),
        ], name="carried")
        repaired = self.seed([
            (BEFORE_RC212, {"due_on": SUMMER_DATE_RIGHT}),
            (IN_WINDOW, {"due_on": SUMMER_DATE_WRONG}),
            (AFTER_FIX, {"due_on": SUMMER_DATE_RIGHT}),
        ], name="repaired")
        report = self.run_scan()
        main_row = self.only(self.rows(report, self.main, "due_on", row=carried.pk), "main row")
        self.assertEqual(main_row["verdict"], "B")
        self.assertEqual(main_row["written"], iso(IN_WINDOW), "Dated by the shifting save.")
        versions = self.rows(report, self.history, "due_on", record=carried.pk)
        self.assertEqual(
            sorted((r["verdict"], r["written"]) for r in versions),
            [("B", iso(IN_WINDOW)), ("B", iso(IN_WINDOW))],
            "Both versions holding the shifted value are B, dated by the save that shifted it.",
        )
        self.assertEqual(self.rows(report, self.main, "due_on", row=repaired.pk), [],
                         "A date put right by hand is correct now.")

    def test_1_431_midnight_utc_written_after_the_fix_is_E_and_a_local_midnight_is_D(self) -> None:
        """
        Scenario 1.431: midnight UTC is a signature, not proof.
        Given: a 00:00 UTC date written after the fix, and a Berlin-midnight date
               written inside the window (a client that sent an explicit instant)
        When: the scan runs
        Then: the first is E for the owner (not this incident) and the second is a
              correct date (D), not listed
        """
        self.seed_audit()
        late = self.seed([(AFTER_FIX, {"due_on": utc(2026, 9, 15)})], name="late")
        right = self.seed([(IN_WINDOW, {"due_on": SUMMER_DATE_RIGHT})], name="right")
        report = self.run_scan()
        late_row = self.only(self.rows(report, self.main, "due_on", row=late.pk), "main row")
        self.assertEqual(late_row["verdict"], "E")
        self.assertIn("outside every exposure period", late_row["reason"])
        self.assertEqual(self.rows(report, self.main, "due_on", row=right.pk), [])
        self.assertEqual(self.column(report, self.main, "due_on")["verdicts"]["D"], 1)

    def test_1_432_a_time_of_day_inside_the_window_is_C_and_dst_hours_are_flagged(self) -> None:
        """
        Scenario 1.432: times need proof of the frontend before any correction.
        Given: 09:30 typed inside the window, 02:30 on the autumn DST night typed
               inside it, and 08:15 typed after the fix
        When: the scan runs
        Then: the first two are C — corrected by the Berlin offset, the DST one
              flagged — and the third is D
        """
        self.seed_audit()
        typed = self.seed([(IN_WINDOW, {"meets_at": TIME_WRONG})], name="typed")
        night = self.seed([(IN_WINDOW, {"meets_at": DST_FALL_BACK})], name="night")
        later = self.seed([(AFTER_FIX, {"meets_at": utc(2026, 9, 2, 8, 15)})], name="later")
        report = self.run_scan()
        typed_row = self.only(self.rows(report, self.main, "meets_at", row=typed.pk), "main row")
        self.assertEqual((typed_row["verdict"], typed_row["corrected"]), ("C", iso(TIME_RIGHT)))
        self.assertFalse(typed_row["dst_ambiguous"])
        night_row = self.only(self.rows(report, self.main, "meets_at", row=night.pk), "main row")
        self.assertEqual(night_row["verdict"], "C")
        self.assertTrue(night_row["dst_ambiguous"], "02:30 on 25 Oct names two instants.")
        later_row = self.only(self.rows(report, self.main, "meets_at", row=later.pk), "main row")
        self.assertEqual(later_row["verdict"], "D")

    def test_1_433_a_write_in_the_gap_before_the_first_affected_row_is_E(self) -> None:
        """
        Scenario 1.433: inside a bracket nobody knows which code wrote.
        Given: a 00:00 UTC date written after the last pre-incident row and before
               the first affected one
        When: the scan runs
        Then: it is E, with the gap named as the reason
        """
        self.seed_audit()
        gap = self.seed([(START_GAP, {"due_on": SUMMER_DATE_WRONG})], name="gap")
        report = self.run_scan()
        row = self.only(self.rows(report, self.main, "due_on", row=gap.pk), "main row")
        self.assertEqual(row["verdict"], "E")
        self.assertIn("gap", row["reason"])

    def test_1_434_without_history_created_at_and_edited_at_bracket_the_write(self) -> None:
        """
        Scenario 1.434: a table without lex history falls back to its row stamps.
        Given: a raw table with two 00:00 UTC dates — one row created and last edited
               inside the window, one created before rc212 and edited inside it
        When: the scan runs
        Then: the first is B; the second is E, because its date may predate the window
        """
        self.seed_audit()
        raw = self.make_raw_table()
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO "{raw}" (due_on, created_at, edited_at) '
                "VALUES (%s, %s, %s), (%s, %s, %s) RETURNING id",
                [SUMMER_DATE_WRONG, IN_WINDOW, IN_WINDOW + dt.timedelta(days=1),
                 SUMMER_DATE_WRONG, BEFORE_RC212, IN_WINDOW],
            )
            inside, straddling = (row[0] for row in cursor.fetchall())
        report = self.run_scan(tables=[raw])
        self.assertEqual(self.column(report, raw, "due_on")["provenance"], "bracket")
        self.assertEqual(self.only(self.rows(report, raw, "due_on", row=inside), "row")["verdict"], "B")
        straddle = self.only(self.rows(report, raw, "due_on", row=straddling), "row")
        self.assertEqual(straddle["verdict"], "E")
        self.assertIn("inside and outside", straddle["reason"])

    def test_1_435_the_command_writes_its_report_and_nothing_else(self) -> None:
        """
        Scenario 1.435: `lex incident_tz scan` reports and leaves the data alone.
        Given: an audit log and one shifted date
        When: the command runs, then runs again with a stated --window
        Then: scan.json and scan.md are written, the saved JSON renders the same
              Markdown on its own (--render, as after a run inside a pod), the data
              and history are unchanged, and a stated window replaces the audit
              log's period
        """
        self.seed_audit()
        item = self.seed([(IN_WINDOW, {"due_on": SUMMER_DATE_WRONG})])
        before = list(IncidentScanItem.objects.values_list("pk", "due_on", "edited_at"))
        versions_before = self.History.objects.count()
        table_args = ["--table", self.main, "--table", self.history, "--table", self.meta]
        with tempfile.TemporaryDirectory() as out, tempfile.TemporaryDirectory() as rendered:
            call_command("incident_tz", "scan", "--out", out, *table_args, stdout=StringIO())
            with open(os.path.join(out, "scan.json"), encoding="utf-8") as handle:
                report = json.load(handle)
            with open(os.path.join(out, "scan.md"), encoding="utf-8") as handle:
                markdown = handle.read()
            self.assertEqual(
                tzscan.main(["--render", os.path.join(out, "scan.json"), "--out", rendered]), 0
            )
            with open(os.path.join(rendered, "scan.md"), encoding="utf-8") as handle:
                self.assertEqual(handle.read(), markdown, "The JSON must carry the whole report.")
        self.assertTrue(report["meta"]["read_only"])
        row = self.only(self.rows(report, self.main, "due_on", row=item.pk), "main row")
        self.assertEqual(row["verdict"], "B")
        self.assertIn("## B: dates stored at midnight UTC", markdown)
        self.assertIn(self.main, markdown)
        self.assertEqual(list(IncidentScanItem.objects.values_list("pk", "due_on", "edited_at")),
                         before, "The scan must not change any record.")
        self.assertEqual(self.History.objects.count(), versions_before, "Nor any history.")

        with tempfile.TemporaryDirectory() as out:
            call_command(
                "incident_tz", "scan", "--out", out, *table_args,
                "--window", "2026-09-01T00:00Z/2026-09-30T00:00Z", stdout=StringIO(),
            )
            with open(os.path.join(out, "scan.json"), encoding="utf-8") as handle:
                stated = json.load(handle)
        self.assertEqual(stated["exposure"]["source"], "manual")
        self.assertEqual(len(stated["exposure"]["audit_periods"]), 1, "The audit view is kept.")
        moved = self.only(self.rows(stated, self.main, "due_on", row=item.pk), "main row")
        self.assertEqual(moved["verdict"], "E", "Outside the stated window it is no longer B.")
