"""In-database activation of future-dated records: the applier and its contract.

Intent: a record scheduled to change at a future instant must change at that instant
whether or not any lex-app process is alive at the time. lex-app records the intent —
a full-snapshot history row with a future ``valid_from`` and a ``SCHEDULED`` meta row —
and PostgreSQL applies it: a pg_cron job calls ``lex_apply_due_activations()`` every
minute, which converges the main row to the history row effective *now* and flips the
meta row to ``DONE``. A regression here is silent non-activation: a fee, a rate or a
status that was correctly scheduled and never arrives, noticed weeks later by a wrong
calculation (design §2.1: 47 of 53 instances lose their in-process timer on restart).

The tests call the SQL function directly. pg_cron's only job is to issue that call, and
LEX-135 verified on the dev server that it does; local PostgreSQL has no pg_cron.

Cluster 05o — scenarios 5.110–5.129. Type: I (E2ETestCase, TransactionTestCase).
Covers: lex/core/sql/bitemporal_activation.sql,
        lex/core/migrations/0001_bitemporal_activation.py,
        lex/core/services/activation_applier.py,
        lex/core/services/MetaHistory.py (scheduled_activation_index),
        lex/core/services/bitemporal_signals.py (_schedule_future_activation).
Run: python -m lex pytest lex/test_project/tests/history/test_5o_activation_applier.py -v
"""

from __future__ import annotations

from datetime import timedelta, timezone as dt_timezone
from unittest.mock import patch

import pytest
from django.db import connection
from django.utils import timezone
from lex.core.services import activation_applier
from lex.core.services.activation_applier import (
    apply_due_activations,
    applier_is_alive,
    bitemporal_tables,
    clear_heartbeat,
    pending_activations,
    read_heartbeat,
    record_heartbeat,
)
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import HistAtomicCalc, HistSimpleItem

pytestmark = pytest.mark.history

_SKIP_NOT_PG = "the in-database applier is PostgreSQL-only (design §8.13)"
_SKIP_NO_L2 = "MetaHistorical* (L2) is not wired on HistSimpleItem in this environment"

_SCHEDULE = "lex.process_admin.utils.local_scheduler.LocalSchedulerBackend.schedule"
_NOW = "django.utils.timezone.now"


def _l2_wired() -> bool:
    return hasattr(getattr(HistSimpleItem, "history", None), "model") and hasattr(
        HistSimpleItem.history.model, "meta_history"
    )


class TestCluster05o_ActivationApplier(E2ETestCase):
    """Cluster 05o: the database applies scheduled changes; lex-app only records them."""

    e2e_models = [HistSimpleItem, HistAtomicCalc]

    # ── plumbing ─────────────────────────────────────────────────────────

    def setUp(self):
        super().setUp()
        if connection.vendor != "postgresql":
            self.skipTest(_SKIP_NOT_PG)
        if not _l2_wired():
            self.skipTest(_SKIP_NO_L2)
        self.meta_model = HistSimpleItem.history.model.meta_history.model
        self.main_table = HistSimpleItem._meta.db_table
        self.history_table = HistSimpleItem.history.model._meta.db_table
        self.meta_table = self.meta_model._meta.db_table
        # E2ETestCase.setUp/tearDown clear the heartbeat around every test. This batch
        # starts each test from "the applier is alive" — the production state it is
        # about; tests that need the opposite clear the heartbeat themselves.
        self._heartbeat()

    # -- heartbeat -----------------------------------------------------

    def _heartbeat(self, age: timedelta = timedelta(0)) -> None:
        record_heartbeat(last_run_at=timezone.now() - age)

    def _no_heartbeat(self) -> None:
        clear_heartbeat()

    # -- time-travelling saves -------------------------------------------
    #
    # PostgreSQL's now() cannot be patched, so "the moment has arrived" is produced
    # the other way round: the record is created and scheduled *in the past*, by
    # patching lex-app's clock during the save. From lex-app's point of view at save
    # time the row is future-dated (→ SCHEDULED, main row untouched); from the
    # database's real clock it is already due. This is exactly the state a restart
    # leaves behind, minus the restart.

    def _create_in_past(self, ago: timedelta, **fields) -> HistSimpleItem:
        with patch(_NOW, return_value=timezone.now() - ago):
            return HistSimpleItem.objects.create(**fields)

    def _schedule_due(self, item: HistSimpleItem, due_for: timedelta, **fields):
        """Save a row whose valid_from is ``due_for`` in the past but which lex-app
        scheduled (and did not apply) because its clock read an hour earlier."""
        real_now = timezone.now()
        valid_from = real_now - due_for
        with patch(_NOW, return_value=valid_from - timedelta(hours=1)):
            for name, value in fields.items():
                setattr(item, name, value)
            item._history_date = valid_from
            item.save()
        return self._latest_l1(item)

    def _schedule_future(self, item: HistSimpleItem, ahead: timedelta, **fields):
        """A genuinely future-dated save, under the real clock."""
        for name, value in fields.items():
            setattr(item, name, value)
        item._history_date = timezone.now() + ahead
        item.save()
        return self._latest_l1(item)

    def _latest_l1(self, item):
        return (
            HistSimpleItem.history.filter(id=item.pk)
            .order_by("-valid_from", "-history_id")
            .first()
        )

    def _metas(self, l1):
        return self.meta_model.objects.filter(history_object_id=l1.history_id)

    def _status(self, l1) -> set[str]:
        return set(self._metas(l1).values_list("meta_task_status", flat=True))

    def _assert_flipped(self, l1, msg: str = "") -> None:
        """The mirror-exact contract (§2.4): every SCHEDULED version of the row is DONE
        and no other version was touched. A row re-chained by a later save *after* its
        own time has passed carries a NONE version too; both paths leave it alone."""
        statuses = self._status(l1)
        self.assertIn("DONE", statuses, msg or "the scheduled version must be DONE")
        self.assertNotIn("SCHEDULED", statuses, msg or "no SCHEDULED version may remain")

    def _main(self, item):
        return HistSimpleItem.objects.filter(pk=item.pk).first()

    def _sql(self, statement: str, params=None):
        with connection.cursor() as cursor:
            cursor.execute(statement, params)
            if cursor.description:
                return cursor.fetchall()
        return None

    def _notices(self) -> list[str]:
        connection.ensure_connection()
        return [str(n) for n in getattr(connection.connection, "notices", [])]

    def _clear_notices(self) -> None:
        connection.ensure_connection()
        notices = getattr(connection.connection, "notices", None)
        if notices is not None:
            del notices[:]

    # ── 5.110 / 5.111  the producer ──────────────────────────────────────

    def test_5_110_future_save_with_live_applier_leaves_only_the_meta_row(self):
        """
        Scenario 5.110: with the database applier alive, a future-dated save arms nothing
        in-process.
        Given: the applier's heartbeat is fresh
        When: a record is saved with valid_from one hour ahead, under CELERY_ACTIVE false
              and again under CELERY_ACTIVE true
        Then: the meta row is SCHEDULED and named for the database applier; no local
              timer is scheduled and no PeriodicTask row is created
        """
        from django_celery_beat.models import PeriodicTask

        item = self._create_in_past(timedelta(hours=1), name="fee", value=150)
        with patch(_SCHEDULE) as schedule:
            l1 = self._schedule_future(item, timedelta(hours=1), value=125)
        self.assertEqual(self._status(l1), {"SCHEDULED"},
                         "the meta row is the record of intent and must be SCHEDULED")
        self.assertTrue(
            all(n.startswith("db_applier_") for n in
                self._metas(l1).values_list("meta_task_name", flat=True)),
            "meta_task_name must say who will act on the row",
        )
        schedule.assert_not_called()

        before = PeriodicTask.objects.count()
        with patch.dict("os.environ", {"CELERY_ACTIVE": "true"}, clear=False):
            l1b = self._schedule_future(item, timedelta(hours=2), value=130)
        self.assertEqual(self._status(l1b), {"SCHEDULED"})
        self.assertEqual(PeriodicTask.objects.count(), before,
                         "no Celery beat row when the database applier is alive")
        self.assertEqual(self._main(item).value, 150,
                         "a future-dated save must not touch the main row")

    def test_5_111_future_save_without_live_applier_keeps_the_legacy_timer(self):
        """
        Scenario 5.111: without a live applier the save behaves exactly as before.
        Given: no heartbeat row at all, then a heartbeat older than the liveness window
        When: a record is saved with valid_from one hour ahead
        Then: the local scheduler is armed once per save and the meta row is SCHEDULED
              with the legacy task name — the fallback lex-app has always had
        """
        item = self._create_in_past(timedelta(hours=1), name="fee", value=150)

        self._no_heartbeat()
        self.assertFalse(applier_is_alive(), "no heartbeat row means not alive")
        with patch(_SCHEDULE) as schedule:
            l1 = self._schedule_future(item, timedelta(hours=1), value=125)
        schedule.assert_called_once()
        self.assertEqual(self._status(l1), {"SCHEDULED"})
        self.assertTrue(all(n.startswith("local_thread_") for n in
                            self._metas(l1).values_list("meta_task_name", flat=True)))

        self._heartbeat(age=activation_applier.liveness_window() + timedelta(minutes=1))
        self.assertFalse(applier_is_alive(), "a stale heartbeat means not alive")
        other = self._create_in_past(timedelta(hours=1), name="rate", value=3)
        with patch(_SCHEDULE) as schedule:
            self._schedule_future(other, timedelta(hours=2), value=4)
        schedule.assert_called_once()

        with patch.dict("os.environ", {activation_applier.LIVENESS_WINDOW_ENV: "7200"}):
            self.assertTrue(applier_is_alive(),
                            "the window is configurable; two hours makes the same heartbeat fresh")

    # ── 5.112 – 5.114  discovery ─────────────────────────────────────────

    def test_5_112_discovery_finds_the_triple_from_the_catalog_alone(self):
        """
        Scenario 5.112: the applier learns which tables are bitemporal from the catalog.
        Given: the fixture's main, history and meta tables, and no content-type row
        When: lex_bitemporal_tables() runs
        Then: it returns the fixture's triple with the primary-key column, and the
              partial SCHEDULED index the scan relies on exists on the meta table
        """
        from django.contrib.contenttypes.models import ContentType

        ContentType.objects.filter(app_label="lex_app", model="histsimpleitem").delete()

        triples = {(t["main_table"], t["history_table"], t["meta_table"], t["pk_column"])
                   for t in bitemporal_tables()}
        self.assertIn(
            (self.main_table, self.history_table, self.meta_table, "id"), triples,
            "the fixture must be discovered from table shape and naming, not from Django",
        )
        self.assertNotIn(
            "lex_activation_applier_state", {t[0] for t in triples},
            "the heartbeat table is not bitemporal",
        )

        rows = self._sql(
            "SELECT indexname, indexdef FROM pg_indexes WHERE tablename = %s "
            "AND indexname LIKE 'lexsched_%%'",
            [self.meta_table],
        )
        self.assertEqual(len(rows), 1, "one partial index per meta table (design §5.5)")
        self.assertIn("meta_task_status", rows[0][1])
        self.assertIn("SCHEDULED", rows[0][1])

    def test_5_113_quoted_mixed_case_tables_are_discovered_and_applied(self):
        """
        Scenario 5.113: real project app labels contain capitals, so the physical names
        are case-sensitive quoted identifiers.
        Given: a bitemporal triple whose names carry capitals, built in SQL
        When: discovery and one applier tick run
        Then: the triple is found, its due row is applied to the main table and its meta
              row flipped — an unquoted lookup would have skipped it silently (§8.20)
        """
        main, hist, meta = "CaseApp_cased", "CaseApp_historicalcased", "CaseApp_cased_meta_history"
        self.addCleanup(self._sql, f'DROP TABLE IF EXISTS "{meta}", "{hist}", "{main}"')
        self._sql(f'CREATE TABLE "{main}" (id integer PRIMARY KEY, name text)')
        self._sql(
            f'CREATE TABLE "{hist}" (history_id serial PRIMARY KEY, id integer, name text, '
            f"valid_from timestamptz, valid_to timestamptz, history_type varchar(1))"
        )
        self._sql(
            f'CREATE TABLE "{meta}" (meta_history_id serial PRIMARY KEY, history_object_id integer, '
            f"meta_task_status varchar(20), sys_from timestamptz, sys_to timestamptz)"
        )
        self._sql(f'INSERT INTO "{main}" (id, name) VALUES (1, %s)', ["old"])
        self._sql(
            f'INSERT INTO "{hist}" (id, name, valid_from, valid_to, history_type) '
            f"VALUES (1, %s, now() - interval '1 minute', NULL, '~')",
            ["new"],
        )
        self._sql(
            f'INSERT INTO "{meta}" (history_object_id, meta_task_status, sys_from) '
            f"VALUES (1, 'SCHEDULED', now())"
        )

        self.assertIn((main, hist, meta, "id"),
                      {(t["main_table"], t["history_table"], t["meta_table"], t["pk_column"])
                       for t in bitemporal_tables()})
        apply_due_activations()
        self.assertEqual(self._sql(f'SELECT name FROM "{main}" WHERE id = 1')[0][0], "new")
        self.assertEqual(self._sql(f'SELECT meta_task_status FROM "{meta}"')[0][0], "DONE")

    def test_5_114_incomplete_triple_is_skipped_with_a_notice_and_others_still_apply(self):
        """
        Scenario 5.114: a meta-shaped table without its siblings must not stop the tick.
        Given: a table with the meta shape and name whose main and history tables do not
               exist, beside a fixture record that is due
        When: discovery and one applier tick run
        Then: the orphan is not in the triples, a NOTICE names it, and the fixture's row
              is applied all the same (§8.17)
        """
        orphan = "nosuchapp_ghost_meta_history"
        self.addCleanup(self._sql, f"DROP TABLE IF EXISTS {orphan}")
        self._sql(f"CREATE TABLE {orphan} (meta_history_id serial PRIMARY KEY, history_object_id integer, "
                  f"meta_task_status varchar(20), sys_from timestamptz, sys_to timestamptz)")
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(minutes=30), value=2)

        self._clear_notices()
        found = {t["meta_table"] for t in bitemporal_tables()}
        self.assertNotIn(orphan, found)
        self.assertTrue(any(orphan in n for n in self._notices()),
                        "the skipped table must be named in a NOTICE, not swallowed")

        self.assertEqual(apply_due_activations(), 1)
        self.assertEqual(self._main(item).value, 2)
        self.assertEqual(self._status(l1), {"DONE"})

    # ── 5.115 / 5.116  visibility ────────────────────────────────────────

    def test_5_115_pending_lists_the_row_before_the_tick_and_not_after(self):
        """
        Scenario 5.115: lex_pending_activations() is the one place to see what waits.
        Given: one due SCHEDULED row and one not yet due
        When: the pending view is read before and after an applier tick
        Then: before, both are listed with the right due flag; after, only the future one
        """
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        due = self._schedule_due(item, timedelta(minutes=30), value=2)
        later = self._schedule_future(item, timedelta(hours=1), value=3)

        by_history = {r["history_id"]: r for r in pending_activations()
                      if r["main_table"] == self.main_table}
        self.assertEqual(by_history[due.history_id]["due"], True)
        self.assertEqual(by_history[due.history_id]["pk"], str(item.pk))
        self.assertEqual(by_history[later.history_id]["due"], False)

        apply_due_activations()
        remaining = {r["history_id"] for r in pending_activations()
                     if r["main_table"] == self.main_table}
        self.assertNotIn(due.history_id, remaining, "applied rows leave the pending view")
        self.assertIn(later.history_id, remaining, "future rows stay until their time")

    def test_5_116_pending_reports_how_long_a_row_has_been_due(self):
        """
        Scenario 5.116: a row nobody applied for a long time must be visible as such.
        Given: a SCHEDULED row whose valid_from is 400 days ago
        When: the pending view is read
        Then: it is due, and due_for is at least 399 days — the alert condition (§5.6)
        """
        item = self._create_in_past(timedelta(days=401), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(days=400), value=2)
        row = next(r for r in pending_activations() if r["history_id"] == l1.history_id)
        self.assertTrue(row["due"])
        self.assertGreaterEqual(row["due_for"], timedelta(days=399))

    # ── 5.117 – 5.122  the applier ───────────────────────────────────────

    def test_5_117_due_row_is_applied_meta_flipped_and_heartbeat_written(self):
        """
        Scenario 5.117: the happy path, with no lex-app process involved in the apply.
        Given: a record whose scheduled change is due
        When: lex_apply_due_activations() runs once
        Then: the main row carries the scheduled values, the meta row is DONE, and the
              heartbeat row records one applied record and no failure
        """
        item = self._create_in_past(timedelta(hours=3), name="fee", value=150)
        l1 = self._schedule_due(item, timedelta(minutes=30), name="fee", value=125)
        self.assertEqual(self._main(item).value, 150, "pre-tick: main row still old")

        self.assertEqual(apply_due_activations(), 1)

        live = self._main(item)
        self.assertEqual((live.name, live.value), ("fee", 125))
        self.assertEqual(self._status(l1), {"DONE"})
        state = read_heartbeat()
        self.assertEqual((state["last_run_applied"], state["last_run_failed"]), (1, 0))
        self.assertLess(abs(timezone.now() - state["last_run_at"]), timedelta(seconds=30))
        self.assertTrue(applier_is_alive(), "the tick itself is the liveness signal")

    def test_5_118_supersede_applies_the_effective_row_and_closes_both(self):
        """
        Scenario 5.118: a later change that is also due wins, and the earlier one is not
        left pending.
        Given: change A due 30 minutes ago and change B due 10 minutes ago, saved after A
        When: one applier tick runs
        Then: the main row shows B, and both meta rows are DONE (§8.1)
        """
        item = self._create_in_past(timedelta(hours=3), name="a0", value=0)
        a = self._schedule_due(item, timedelta(minutes=30), name="A", value=1)
        b = self._schedule_due(item, timedelta(minutes=10), name="B", value=2)

        apply_due_activations()
        self.assertEqual(self._main(item).name, "B")
        self._assert_flipped(a, "the superseded row is no longer pending")
        self._assert_flipped(b)

    def test_5_119_cancelled_and_orphan_rows_are_left_alone(self):
        """
        Scenario 5.119: only SCHEDULED rows are the applier's business.
        Given: a due row whose meta is CANCELLED, and a SCHEDULED meta row whose history
               row is gone
        When: one applier tick runs
        Then: the main row is untouched, statuses are unchanged, nothing raises, and the
              orphan is visible in the pending view with no pk (§8.2)
        """
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(minutes=30), value=2)
        self._metas(l1).update(meta_task_status="CANCELLED")

        other = self._create_in_past(timedelta(hours=3), name="o", value=1)
        l1o = self._schedule_due(other, timedelta(minutes=30), value=9)
        orphan_ids = list(self._metas(l1o).values_list("pk", flat=True))
        self.meta_model.objects.filter(pk__in=orphan_ids).update(history_object_id=None)

        applied = apply_due_activations()
        self.assertEqual(applied, 0)
        self.assertEqual(self._main(item).value, 1)
        self.assertEqual(self._status(l1), {"CANCELLED"})
        self.assertEqual(
            set(self.meta_model.objects.filter(pk__in=orphan_ids)
                .values_list("meta_task_status", flat=True)),
            {"SCHEDULED"},
        )
        orphans = [r for r in pending_activations() if r["meta_history_id"] in orphan_ids]
        self.assertEqual(len(orphans), len(orphan_ids))
        self.assertTrue(all(r["pk"] is None for r in orphans))

    def test_5_120_several_due_rows_converge_to_the_effective_one_in_one_write(self):
        """
        Scenario 5.120: after downtime, three changes for one record are due at once.
        Given: changes A, B, C due 3, 2 and 1 hours ago
        When: one applier tick runs
        Then: the main row shows C, all three meta rows are DONE, and the tick counted
              one record — the end state, never the sequence (§8.3)
        """
        item = self._create_in_past(timedelta(hours=6), name="s0", value=0)
        rows = [self._schedule_due(item, timedelta(hours=h), name=f"s{h}", value=h)
                for h in (3, 2, 1)]

        self.assertEqual(apply_due_activations(), 1)
        live = self._main(item)
        self.assertEqual((live.name, live.value), ("s1", 1))
        for l1 in rows:
            self._assert_flipped(l1)

    def test_5_121_deletion_as_the_effective_record_removes_the_main_row(self):
        """
        Scenario 5.121: a scheduled deletion is a change like any other.
        Given: a due history row of type "-"
        When: one applier tick runs
        Then: the main row is gone and the meta row is DONE (§8.4)
        """
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(minutes=30), value=2)
        HistSimpleItem.history.filter(history_id=l1.history_id).update(history_type="-")

        apply_due_activations()
        self.assertIsNone(self._main(item), "the deletion branch must exist (§8.4)")
        self.assertEqual(self._status(l1), {"DONE"})

    def test_5_122_a_row_due_for_a_year_is_applied_not_skipped(self):
        """
        Scenario 5.122: lateness is not a reason to leave the main table wrong.
        Given: a SCHEDULED row due 400 days ago
        When: one applier tick runs
        Then: it is applied — convergence makes a late apply correct and a skipped one
              wrong indefinitely (§5.3, §8.11)
        """
        item = self._create_in_past(timedelta(days=401), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(days=400), value=2)
        self.assertEqual(apply_due_activations(), 1)
        self.assertEqual(self._main(item).value, 2)
        self.assertEqual(self._status(l1), {"DONE"})

    # ── 5.123 – 5.125  parity with the Python path ───────────────────────

    def _common_columns(self) -> list[str]:
        rows = self._sql(
            "SELECT m.column_name FROM information_schema.columns m "
            "JOIN information_schema.columns h ON h.column_name = m.column_name "
            "AND h.table_schema = m.table_schema AND h.table_name = %s "
            "WHERE m.table_schema = 'public' AND m.table_name = %s ORDER BY m.ordinal_position",
            [self.history_table, self.main_table],
        )
        return [r[0] for r in rows]

    def _row(self, table: str, where_col: str, value) -> dict:
        cols = self._common_columns()
        rows = self._sql(
            f'SELECT {", ".join(cols)} FROM {table} WHERE {where_col} = %s', [value]
        )
        return dict(zip(cols, rows[0])) if rows else {}

    def test_5_123_applier_and_python_task_produce_the_same_end_state(self):
        """
        Scenario 5.123: two activators, one contract — the one test that matters most.
        Given: two records with identical scheduled changes; one activated by the SQL
               applier, the other by activate_history_version (the Celery task)
        When: each runs
        Then: for both, every column the main table shares with the history table equals
              the scheduled snapshot, the meta rows are DONE, no history row was added,
              and Django's view of the fixture's tables is what the catalog discovered
        """
        from lex.lex_app import celery_tasks

        x = self._create_in_past(timedelta(hours=3), name="x0", value=0)
        lx = self._schedule_due(x, timedelta(minutes=30), name="x1", value=11)
        hist_before_x = HistSimpleItem.history.filter(id=x.pk).count()
        self.assertEqual(apply_due_activations(), 1)

        y = self._create_in_past(timedelta(hours=3), name="y0", value=0)
        ly = self._schedule_due(y, timedelta(minutes=30), name="y1", value=11)
        hist_before_y = HistSimpleItem.history.filter(id=y.pk).count()
        result = celery_tasks.activate_history_version(
            HistSimpleItem._meta.app_label, HistSimpleItem._meta.model_name, ly.history_id,
        )
        outcome = result[0] if isinstance(result, tuple) else result
        self.assertEqual(outcome, "success")

        for item, l1, before in ((x, lx, hist_before_x), (y, ly, hist_before_y)):
            snapshot = self._row(self.history_table, "history_id", l1.history_id)
            live = self._row(self.main_table, "id", item.pk)
            self.assertEqual(live, snapshot,
                             "main row must be a copy of the scheduled snapshot on every shared column")
            self.assertEqual(self._status(l1), {"DONE"})
            self.assertEqual(HistSimpleItem.history.filter(id=item.pk).count(), before,
                             "activation mints no history row on either path")

        django_view = (self.main_table, self.history_table, self.meta_table,
                       HistSimpleItem._meta.pk.column)
        self.assertIn(django_view, {(t["main_table"], t["history_table"], t["meta_table"],
                                     t["pk_column"]) for t in bitemporal_tables()},
                      "both activators must agree on which tables exist")

    def test_5_124_activation_touches_nothing_but_the_three_tables(self):
        """
        Scenario 5.124: activation is a pure data write (design §2.3).
        Given: a due row, and an unrelated calculation model with rows
        When: one applier tick runs
        Then: the other model's rows, the record's history rows and the number of meta
              versions are all unchanged
        """
        calc = HistAtomicCalc.objects.create(name="untouched")
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        self._schedule_due(item, timedelta(minutes=30), value=2)
        calc_before = list(HistAtomicCalc.objects.values_list("pk", "name", "is_calculated"))
        hist_before = HistSimpleItem.history.filter(id=item.pk).count()
        meta_before = self.meta_model.objects.count()

        apply_due_activations()
        self.assertEqual(list(HistAtomicCalc.objects.values_list("pk", "name", "is_calculated")),
                         calc_before)
        self.assertEqual(HistSimpleItem.history.filter(id=item.pk).count(), hist_before)
        self.assertEqual(self.meta_model.objects.count(), meta_before,
                         "the flip is in place: no new meta version (§8.12)")
        self.assertEqual(calc.pk, HistAtomicCalc.objects.get(pk=calc.pk).pk)

    def test_5_125_meta_flip_mirrors_the_python_update_exactly(self):
        """
        Scenario 5.125: the applier flips what the Python .update() flips — every
        SCHEDULED version of the history row — and nothing else.
        Given: two SCHEDULED meta versions and one NONE version for the same due row
        When: one applier tick runs
        Then: both SCHEDULED versions are DONE, the NONE version is untouched, and the
              number of versions is unchanged (§2.4, §8.12)
        """
        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(minutes=30), value=2)
        template = self._metas(l1).first()

        def clone(status):
            values = {f.attname: getattr(template, f.attname) for f in self.meta_model._meta.fields
                      if f.attname not in ("meta_history_id",)}
            values["meta_task_status"] = status
            values["meta_task_name"] = None
            values["sys_from"] = timezone.now()
            self.meta_model.objects.create(**values)

        clone("SCHEDULED")
        clone("NONE")
        self.assertEqual(sorted(self._metas(l1).values_list("meta_task_status", flat=True)),
                         ["NONE", "SCHEDULED", "SCHEDULED"])
        count_before = self._metas(l1).count()

        apply_due_activations()
        self.assertEqual(sorted(self._metas(l1).values_list("meta_task_status", flat=True)),
                         ["DONE", "DONE", "NONE"])
        self.assertEqual(self._metas(l1).count(), count_before)

    # ── 5.126 – 5.129  time, idempotence, isolation, boundary ────────────

    def test_5_126_due_is_decided_by_the_instant_not_the_wall_clock(self):
        """
        Scenario 5.126: valid_from is an instant; offsets must not move it.
        Given: a row whose valid_from, written with a +05:00 offset, is 30 minutes in the
               past as an instant but reads as the future on a UTC wall clock; and one
               written with a -05:00 offset that is 30 minutes ahead as an instant but
               reads as the past
        When: one applier tick runs
        Then: the first is applied and the second is not (§8.7)
        """
        plus5 = dt_timezone(timedelta(hours=5))
        minus5 = dt_timezone(timedelta(hours=-5))
        real_now = timezone.now()

        due_item = self._create_in_past(timedelta(hours=3), name="due", value=1)
        due_instant = (real_now - timedelta(minutes=30)).astimezone(plus5)
        with patch(_NOW, return_value=real_now - timedelta(hours=2)):
            due_item.value = 2
            due_item._history_date = due_instant
            due_item.save()
        due_l1 = self._latest_l1(due_item)

        future_item = self._create_in_past(timedelta(hours=3), name="future", value=1)
        future_l1 = self._schedule_future(future_item, timedelta(minutes=30), value=2)
        future_instant = (real_now + timedelta(minutes=30)).astimezone(minus5)
        HistSimpleItem.history.filter(history_id=future_l1.history_id).update(valid_from=future_instant)

        apply_due_activations()
        self.assertEqual(self._main(due_item).value, 2, "+05:00 row is due as an instant")
        self.assertEqual(self._status(due_l1), {"DONE"})
        self.assertEqual(self._main(future_item).value, 1, "-05:00 row is not yet due as an instant")
        self.assertEqual(self._status(future_l1), {"SCHEDULED"})

    def test_5_127_second_tick_and_python_task_change_nothing(self):
        """
        Scenario 5.127: the applier converges; it does not replay.
        Given: a due row applied by one tick
        When: the applier runs again, and then activate_history_version runs for the row
        Then: the second tick applies zero records, the task reports success, and the
              main row is byte-identical throughout (§8.9)
        """
        from lex.lex_app import celery_tasks

        item = self._create_in_past(timedelta(hours=3), name="a", value=1)
        l1 = self._schedule_due(item, timedelta(minutes=30), value=2)
        self.assertEqual(apply_due_activations(), 1)
        first = self._row(self.main_table, "id", item.pk)

        self.assertEqual(apply_due_activations(), 0)
        self.assertEqual(self._row(self.main_table, "id", item.pk), first)

        result = celery_tasks.activate_history_version(
            HistSimpleItem._meta.app_label, HistSimpleItem._meta.model_name, l1.history_id,
        )
        outcome = result[0] if isinstance(result, tuple) else result
        self.assertEqual(outcome, "success")
        self.assertEqual(self._row(self.main_table, "id", item.pk), first)
        self.assertEqual(self._status(l1), {"DONE"})

    def test_5_128_one_failing_record_does_not_stop_the_others(self):
        """
        Scenario 5.128: a poison row costs one record per tick, not the tick.
        Given: two due rows, one of which violates a constraint on the main table
        When: one applier tick runs
        Then: the healthy record is applied, the poison record stays SCHEDULED, the
              heartbeat counts one failure, and a WARNING names the table and pk (§8.10)
        """
        poison = self._create_in_past(timedelta(hours=3), name="poison", value=1)
        lp = self._schedule_due(poison, timedelta(minutes=30), value=5000)
        healthy = self._create_in_past(timedelta(hours=3), name="healthy", value=1)
        lh = self._schedule_due(healthy, timedelta(minutes=30), value=7)
        self.assertEqual(self._main(poison).value, 1, "pre-tick: the synchroniser left the old value")

        # Added after the saves on purpose: a future-dated save writes the main row and
        # the synchroniser immediately restores the old values, so the constraint must
        # not be there yet. From here on only the applier's upsert can violate it.
        constraint = "lex5o_value_ck"
        self.addCleanup(self._sql, f"ALTER TABLE {self.main_table} DROP CONSTRAINT IF EXISTS {constraint}")
        self._sql(f"ALTER TABLE {self.main_table} ADD CONSTRAINT {constraint} CHECK (value < 1000)")

        self._clear_notices()
        self.assertEqual(apply_due_activations(), 1)
        self.assertEqual(self._main(healthy).value, 7)
        self.assertEqual(self._status(lh), {"DONE"})
        self.assertEqual(self._main(poison).value, 1)
        self.assertEqual(self._status(lp), {"SCHEDULED"}, "a failed record stays pending, visibly")
        state = read_heartbeat()
        self.assertEqual((state["last_run_applied"], state["last_run_failed"]), (1, 1))
        self.assertTrue(
            any("lex_apply_due_activations" in n and str(poison.pk) in n for n in self._notices()),
            "the failure must be reported as a WARNING naming the record",
        )

    def test_5_129_boundary_no_lex_app_code_runs_between_save_and_final_state(self):
        """
        Scenario 5.129: the boundary (design §3.2, §6), end to end.
        Given: a live applier and a record saved with a future valid_from
        When: the only step between the save and the assertions is one
              SELECT lex_apply_due_activations() through a raw cursor, with every
              in-process activation entry point instrumented to fail if called
        Then: no timer or beat row was armed at save time, none of the Python entry
              points ran, and all three tables reach their final, consistent state
        """
        from django_celery_beat.models import PeriodicTask

        beat_before = PeriodicTask.objects.count()
        item = self._create_in_past(timedelta(hours=3), name="fee", value=150)
        with patch(_SCHEDULE) as schedule:
            l1 = self._schedule_due(item, timedelta(minutes=30), value=125)
        schedule.assert_not_called()
        self.assertEqual(PeriodicTask.objects.count(), beat_before)
        self.assertEqual(self._main(item).value, 150)
        history_before = HistSimpleItem.history.filter(id=item.pk).count()

        with patch(_SCHEDULE, side_effect=AssertionError("timer armed at apply time")), \
             patch("lex.process_admin.utils.bitemporal_sync.BitemporalSynchronizer.sync_record_for_model",
                   side_effect=AssertionError("Python synchroniser ran at apply time")):
            with connection.cursor() as cursor:
                cursor.execute("SELECT lex_apply_due_activations()")
                applied = cursor.fetchone()[0]

        self.assertEqual(applied, 1)
        self.assertEqual(self._main(item).value, 125, "main: the scheduled value")
        self.assertEqual(HistSimpleItem.history.filter(id=item.pk).count(), history_before,
                         "history: unchanged")
        self.assertEqual(self._status(l1), {"DONE"}, "meta: DONE")
