"""
Consolidated integration tests for the bitemporal 3-layer architecture.

These tests exercise Main Table ↔ History (Level 1) ↔ Meta-History (Level 2)
interactions against a real SQLite database.  They verify:

- Valid-time chaining (``valid_from`` / ``valid_to`` on History rows)
- System-time auditing (``sys_from`` / ``sys_to`` on Meta-History rows)
- ``get_queryset_as_of`` correctness for both valid-time and system-time
- Retroactive corrections, deletions, and chain repair
- Edge cases: future-valid rows, validity gaps, sequential edits
- REST API history endpoint (``/history/<pk>``) with ``as_of`` parameter

Consolidated from
-----------------
- ``core/tests/test_bitemporal.py``            → `BitemporalLogicTest`
- ``core/tests/test_bitemporal_trace.py``       → `BitemporalTraceTest`
- ``core/tests/test_bitemporal_scenarios.py``   → `BitemporalScenarioTest`
- ``core/tests/test_bitemporal_robustness.py``  → `BitemporalRobustnessTest`
- ``core/tests/test_bitemporal_as_of.py``       → `BitemporalAsOfTest`
- ``core/tests/test_bitemporal_history_edit.py`` → `BitemporalHistoryEditTest`
- ``core/tests/test_bitemporal_history_deletion_as_of.py``
                                                → `BitemporalHistoryDeletionAsOfTest`
- ``core/tests/test_history_api.py``            → `TestHistoryTimelineAPI`

How to run
----------
.. code-block:: bash

    python -m django test lex.tests.integration.test_bitemporal \\
        --settings=lex.process_admin.tests.django_test_settings -v2
"""

import datetime
from datetime import timedelta
from importlib import reload
from unittest.mock import MagicMock, patch

from django.db import connection, models
from django.test import TransactionTestCase
from django.utils import timezone
from lex.core.models.LexModel import LexModel
from lex.core.services.Bitemporal import get_queryset_as_of
from lex.process_admin.utils.bitemporal_sync import BitemporalSynchronizer
from lex.tests.integration._bitemporal_test_case import DynamicBitemporalModelTestCase


# ====================================================================
#  Test models — one per original file to avoid table-name collisions
# ====================================================================

class TestBitemporalModel(LexModel):
    """Used by BitemporalLogicTest (from test_bitemporal.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class TraceModel(LexModel):
    """Used by BitemporalTraceTest (from test_bitemporal_trace.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class ScenarioTestModel(LexModel):
    """Used by BitemporalScenarioTest (from test_bitemporal_scenarios.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class RobustnessTestModel(LexModel):
    """Used by BitemporalRobustnessTest (from test_bitemporal_robustness.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class AsOfTestModel(LexModel):
    """Used by BitemporalAsOfTest (from test_bitemporal_as_of.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class HistEditModel(LexModel):
    """Used by BitemporalHistoryEditTest (from test_bitemporal_history_edit.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class HistDelAsOfModel(LexModel):
    """Used by BitemporalHistoryDeletionAsOfTest (from test_bitemporal_history_deletion_as_of.py)."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class SyncBypassModel(LexModel):
    """Used to verify when history saves should skip or invoke main-table sync."""
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "rest_framework_api_key"


# ====================================================================
#  Shared helpers
# ====================================================================

class _BitemporalDebugMixin:
    """Debug helpers shared across bitemporal test classes."""

    def _ts(self, dt):
        """Format datetime for debug output."""
        if dt is None:
            return "∞"
        return dt.strftime("%H:%M:%S")

    def _dump_state(self, label, model_class, history_model, meta_model):
        """Print current state of all three tables for debugging."""
        print(f"\n{'=' * 60}")
        print(f"  {label}")
        print(f"{'=' * 60}")

        print("  Main Table:")
        for obj in model_class.objects.all():
            print(f"    pk={obj.pk}  name={obj.name}")

        print("  History Table:")
        for h in history_model.objects.order_by("valid_from", "history_id"):
            print(
                f"    history_id={h.history_id}  name={h.name}  "
                f"valid=[{self._ts(h.valid_from)}, {self._ts(h.valid_to)})  "
                f"type={h.history_type}"
            )

        print("  Meta-History Table:")
        for m in meta_model.objects.order_by("sys_from", "meta_history_id"):
            print(
                f"    meta_id={m.meta_history_id}  "
                f"history_object_id={m.history_object_id}  "
                f"name={m.name}  "
                f"valid=[{self._ts(m.valid_from)}, {self._ts(m.valid_to)})  "
                f"sys=[{self._ts(m.sys_from)}, {self._ts(m.sys_to)})  "
                f"type={m.meta_history_type}"
            )


# ====================================================================
#  From test_bitemporal.py — Core regression coverage
# ====================================================================

class BitemporalLogicTest(DynamicBitemporalModelTestCase):
    """Core regression coverage for strict bitemporal chaining behavior."""

    model_class = TestBitemporalModel

    def test_scenario_three_tables(self):
        """A corrected history row should rewire valid-time and preserve system-time history."""
        base_time = datetime.datetime(2024, 4, 1, 12, 0, 0)

        with patch("django.utils.timezone.now", return_value=base_time):
            obj = TestBitemporalModel.objects.create(name="melih")

        h1 = obj.history.first()
        self.assertEqual(h1.name, "melih")
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(h1.valid_to)

        m1 = h1.meta_history.first()
        self.assertEqual(m1.history_object, h1)
        self.assertTrue(abs((m1.sys_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(m1.sys_to)

        t1 = base_time + timedelta(minutes=5)
        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "melih2"
            obj.save()

        h1.refresh_from_db()
        h2 = obj.history.latest("valid_from")
        self.assertEqual(h2.name, "melih2")
        self.assertTrue(abs((h1.valid_to - t1).total_seconds()) < 1.0)
        self.assertIsNone(h2.valid_to)

        t_correction = base_time - timedelta(hours=1)
        t2 = base_time + timedelta(minutes=8)
        with patch("django.utils.timezone.now", return_value=t2):
            h2.valid_from = t_correction
            h2.save()

        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertTrue(abs((h2.valid_from - t_correction).total_seconds()) < 1.0)
        self.assertTrue(abs((h2.valid_to - base_time).total_seconds()) < 1.0)
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(h1.valid_to)

        qs_initial = get_queryset_as_of(TestBitemporalModel, base_time + timedelta(minutes=1))
        self.assertEqual(qs_initial.count(), 1)
        self.assertEqual(qs_initial.first().name, "melih")

        qs_after_update = get_queryset_as_of(TestBitemporalModel, t1 + timedelta(minutes=1))
        rows_after_update = list(qs_after_update)
        self.assertEqual(len(rows_after_update), 1)
        self.assertEqual(rows_after_update[0].name, "melih")

        qs_system_before_correction = get_queryset_as_of(
            TestBitemporalModel.history.model,
            t1 + timedelta(minutes=1),
        )
        self.assertGreaterEqual(qs_system_before_correction.count(), 1)
        self.assertIn(
            "melih2",
            [meta_row.name for meta_row in qs_system_before_correction],
        )

        qs_after_correction = get_queryset_as_of(TestBitemporalModel, t2 + timedelta(minutes=1))
        self.assertEqual(qs_after_correction.count(), 1)
        self.assertEqual(qs_after_correction.first().name, "melih")

        qs_system_after_correction = get_queryset_as_of(
            TestBitemporalModel.history.model,
            t2 + timedelta(minutes=1),
        )
        self.assertGreaterEqual(qs_system_after_correction.count(), 2)
        self.assertIn(
            "melih2",
            [meta_row.name for meta_row in qs_system_after_correction],
        )
        corrected_meta = next(
            meta_row
            for meta_row in qs_system_after_correction
            if meta_row.name == "melih2"
        )
        self.assertTrue(
            abs((corrected_meta.valid_from - t_correction).total_seconds()) < 1.0
        )


# ====================================================================
#  From test_bitemporal_trace.py — Canonical user trace
# ====================================================================

class BitemporalTraceTest(DynamicBitemporalModelTestCase):
    """Regression coverage for the canonical user-provided bitemporal trace."""

    model_class = TraceModel

    def test_trace_scenario(self):
        """Correcting a later row to start at 13:00 should extend the previous row to 13:00."""
        t_1200 = datetime.datetime(2026, 1, 26, 12, 0, 0)
        t_1205 = datetime.datetime(2026, 1, 26, 12, 5, 0)
        t_1208 = datetime.datetime(2026, 1, 26, 12, 8, 0)
        t_1300 = datetime.datetime(2026, 1, 26, 13, 0, 0)

        with patch("django.utils.timezone.now", return_value=t_1200):
            obj = TraceModel.objects.create(name="melih")

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 1)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_from, t_1200)
        self.assertIsNone(h_objs[0].valid_to)

        with patch("django.utils.timezone.now", return_value=t_1205):
            obj.name = "melih2"
            obj.save()

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 2)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1205)
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1205)
        self.assertIsNone(h_objs[1].valid_to)

        with patch("django.utils.timezone.now", return_value=t_1208):
            h_melih2 = h_objs[1]
            h_melih2.valid_from = t_1300
            h_melih2.save()

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 2)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1300)
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1300)
        self.assertIsNone(h_objs[1].valid_to)

        meta_objs = list(self.MetaModel.objects.order_by("sys_from", "id"))
        self.assertGreaterEqual(len(meta_objs), 2)
        self.assertTrue(any(meta_row.name == "melih" for meta_row in meta_objs))
        self.assertTrue(any(meta_row.name == "melih2" for meta_row in meta_objs))


# ====================================================================
#  From test_bitemporal_scenarios.py — Multi-step lifecycle
# ====================================================================

class BitemporalScenarioTest(DynamicBitemporalModelTestCase):
    """Regression tests for representative bitemporal lifecycle scenarios."""

    model_class = ScenarioTestModel

    def test_user_scenario(self):
        """A correction that moves a future row later should re-close its predecessor."""
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0)
        T12_05 = T12_00 + timedelta(minutes=5)
        T12_08 = T12_00 + timedelta(minutes=8)
        T13_00 = T12_00 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="melih")

        with patch("django.utils.timezone.now", return_value=T12_05):
            obj.name = "melih2"
            obj.save()

        with patch("django.utils.timezone.now", return_value=T12_08):
            h_melih2 = self.HistoryModel.objects.get(name="melih2")
            h_melih2.valid_from = T13_00
            h_melih2.save()

        h_rows = list(self.HistoryModel.objects.all().order_by("valid_from"))
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(h_rows[0].name, "melih")
        self.assertEqual(h_rows[0].valid_from, T12_00)
        self.assertEqual(h_rows[0].valid_to, T13_00)
        self.assertEqual(h_rows[1].name, "melih2")
        self.assertEqual(h_rows[1].valid_from, T13_00)
        self.assertIsNone(h_rows[1].valid_to)

        m_melih_closed = self.MetaModel.objects.filter(
            name="melih", valid_to__isnull=False
        ).latest("sys_from")
        self.assertEqual(m_melih_closed.valid_to, T13_00)
        self.assertEqual(m_melih_closed.sys_from, T12_05)
        self.assertIsNone(m_melih_closed.sys_to)

        current_obj = ScenarioTestModel.objects.get(pk=obj.pk)
        self.assertEqual(current_obj.name, "melih")

        T12_10 = T12_00 + timedelta(minutes=10)
        T12_30 = T12_00 + timedelta(minutes=30)

        with patch("django.utils.timezone.now", return_value=T12_10):
            h_melih2.valid_from = T12_30
            h_melih2.save()

        m_melih_closed.refresh_from_db()
        self.assertEqual(m_melih_closed.valid_to, T12_30)
        self.assertEqual(m_melih_closed.sys_from, T12_05)

    def test_deletion(self):
        """Deleting the live row should close the previously valid history interval."""
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0)
        T12_10 = T12_00 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="to_be_deleted")

        self.assertEqual(ScenarioTestModel.objects.count(), 1)

        with patch("django.utils.timezone.now", return_value=T12_10):
            obj.delete()

        self.assertEqual(ScenarioTestModel.objects.count(), 0)

        h_rows = list(self.HistoryModel.objects.all().order_by("history_id"))
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(h_rows[1].history_type, "-")
        self.assertEqual(h_rows[1].valid_from, T12_10)
        self.assertEqual(h_rows[0].valid_to, T12_10)

    def test_retroactive_creation(self):
        """A retroactive `_history_date` should affect valid time, not system time."""
        T12_00 = datetime.datetime(2024, 1, 1, 12, 0, 0)
        T11_00 = T12_00 - timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T12_00):
            retro_obj = ScenarioTestModel(name="retro")
            retro_obj._history_date = T11_00
            retro_obj.save()

        h1 = self.HistoryModel.objects.first()
        self.assertEqual(h1.valid_from, T11_00)
        self.assertIsNone(h1.valid_to)

        m1 = self.MetaModel.objects.first()
        self.assertEqual(m1.sys_from, T12_00)
        self.assertIsNone(m1.sys_to)


# ====================================================================
#  From test_bitemporal_robustness.py — Edge cases
# ====================================================================

class BitemporalRobustnessTest(DynamicBitemporalModelTestCase):
    """Edge-case coverage for future-valid rows and gaps in valid-time continuity."""

    model_class = RobustnessTestModel

    def test_future_insert_sync(self):
        """A row that is only valid in the future should not appear in the live main table yet."""
        t0 = datetime.datetime(2025, 1, 1, 12, 0, 0)
        t_future = t0 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = RobustnessTestModel(name="future_obj")
            obj._history_date = t_future
            obj.save()

        self.assertEqual(
            RobustnessTestModel.objects.count(),
            0,
            "Main table should be empty when the only row is future-valid.",
        )

    def test_gap_in_validity(self):
        """A synchronization during a validity gap should clear the main table."""
        t0 = datetime.datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t0 + timedelta(minutes=20)
        t_gap = t0 + timedelta(minutes=15)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = RobustnessTestModel.objects.create(name="MsgA")

        original_id = obj.id

        with patch("django.utils.timezone.now", return_value=t1):
            obj.delete()

        with patch("django.utils.timezone.now", return_value=t0):
            replacement = RobustnessTestModel(pk=original_id, name="MsgB")
            replacement._history_date = t2
            replacement.save()

        with patch("django.utils.timezone.now", return_value=t_gap):
            gap_history = self.HistoryModel.objects.filter(name="MsgB").latest("history_id")
            gap_history.save()

        self.assertEqual(RobustnessTestModel.objects.count(), 0)


class BitemporalSyncBypassTest(DynamicBitemporalModelTestCase):
    """Regression coverage for selective main-table synchronization."""

    model_class = SyncBypassModel

    def setUp(self):
        import lex.process_admin.settings as process_admin_settings

        self._process_admin_settings_module = process_admin_settings
        self._original_admin_site = process_admin_settings.__dict__.get("adminSite")
        self._original_process_admin_site = process_admin_settings.__dict__.get("processAdminSite")

        self.mock_admin_site = MagicMock()
        self.mock_process_admin_site = MagicMock()
        self.mock_admin_site.is_registered.return_value = False
        self.mock_admin_site.register = MagicMock()
        self.mock_process_admin_site.register = MagicMock()
        process_admin_settings.__dict__["adminSite"] = self.mock_admin_site
        process_admin_settings.__dict__["processAdminSite"] = self.mock_process_admin_site
        self.addCleanup(self._restore_process_admin_sites)
        super().setUp()

    def _restore_process_admin_sites(self):
        self._process_admin_settings_module.__dict__["adminSite"] = self._original_admin_site
        self._process_admin_settings_module.__dict__["processAdminSite"] = self._original_process_admin_site

    def test_live_model_saves_skip_history_to_main_sync(self):
        """Current-time main-model saves should not pay for a redundant resync."""
        t0 = timezone.make_aware(datetime.datetime(2025, 2, 1, 12, 0, 0))
        t1 = t0 + timedelta(minutes=5)

        with patch(
            "lex.process_admin.utils.bitemporal_sync.BitemporalSynchronizer.sync_record_for_model"
        ) as sync_mock:
            with patch("django.utils.timezone.now", return_value=t0):
                obj = SyncBypassModel.objects.create(name="initial")

            with patch("django.utils.timezone.now", return_value=t1):
                obj.name = "updated"
                obj.save()

        self.assertEqual(sync_mock.call_count, 0)
        self.assertEqual(SyncBypassModel.objects.get(pk=obj.pk).name, "updated")

        history_rows = list(obj.history.order_by("valid_from", "history_id"))
        self.assertEqual([row.name for row in history_rows], ["initial", "updated"])
        self.assertTrue(abs((history_rows[0].valid_to - t1).total_seconds()) < 1.0)
        self.assertIsNone(history_rows[1].valid_to)

    def test_future_dated_main_save_still_uses_sync(self):
        """Future-valid rows still need synchronizer cleanup of the live table."""
        t0 = timezone.make_aware(datetime.datetime(2025, 2, 1, 12, 0, 0))
        t_future = t0 + timedelta(hours=1)

        with patch.object(
            BitemporalSynchronizer,
            "sync_record_for_model",
            wraps=BitemporalSynchronizer.sync_record_for_model,
        ) as sync_spy:
            with patch("django.utils.timezone.now", return_value=t0):
                obj = SyncBypassModel(name="future")
                obj._history_date = t_future
                obj.save()

        self.assertEqual(sync_spy.call_count, 1)
        self.assertEqual(SyncBypassModel.objects.count(), 0)

        future_history = obj.history.filter(history_type="+").latest("history_id")
        self.assertEqual(future_history.valid_from, t_future)


# ====================================================================
#  From test_bitemporal_as_of.py — As-of query correctness
# ====================================================================

class BitemporalAsOfTest(DynamicBitemporalModelTestCase):
    """Tests for valid-time and system-time as-of queries."""

    model_class = AsOfTestModel

    def test_as_of_validity(self):
        """Future-valid rows stay hidden until the requested valid-time instant."""
        T0 = datetime.datetime(2025, 1, 1, 12, 0, 0)
        T_Future = T0 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T0):
            future_obj = AsOfTestModel(name="FutureVal")
            future_obj._history_date = T_Future
            future_obj.save()

        with patch("django.utils.timezone.now", return_value=T0):
            self.assertEqual(AsOfTestModel.objects.count(), 0)

            qs_now = get_queryset_as_of(AsOfTestModel, T0)
            self.assertEqual(qs_now.count(), 0, "as_of=Now should exclude future rows")

            qs_future = get_queryset_as_of(AsOfTestModel, T_Future)
            self.assertEqual(qs_future.count(), 1, "as_of=Future should show the row")
            self.assertEqual(qs_future.first().name, "FutureVal")

    def test_as_of_historical(self):
        """As-of on the main model should return the row valid at that moment."""
        T0 = datetime.datetime(2025, 1, 1, 10, 0, 0)
        T1 = T0 + timedelta(hours=1)
        T2 = T0 + timedelta(hours=2)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = AsOfTestModel(name="OldVal")
            obj._history_date = T0
            obj.save()

        with patch("django.utils.timezone.now", return_value=T1):
            obj.name = "NewVal"
            obj._history_date = T1
            obj.save()

        with patch("django.utils.timezone.now", return_value=T2):
            qs_past = get_queryset_as_of(AsOfTestModel, T0 + timedelta(minutes=30))
            self.assertEqual(qs_past.first().name, "OldVal")

            qs_current = get_queryset_as_of(AsOfTestModel, T1 + timedelta(minutes=30))
            self.assertEqual(qs_current.first().name, "NewVal")

    def test_as_of_with_history_model_class(self):
        """Passing the history model should query system-time knowledge via meta-history."""
        T0 = datetime.datetime(2025, 1, 1, 10, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = AsOfTestModel.objects.create(name="Original")

        T1 = T0 + timedelta(hours=1)
        with patch("django.utils.timezone.now", return_value=T1):
            obj.name = "Changed"
            obj.save()

        qs_valid = get_queryset_as_of(AsOfTestModel, T1)
        self.assertEqual(qs_valid.first().name, "Changed")

        qs_sys_t0 = get_queryset_as_of(self.HistoryModel, T0)
        self.assertEqual(qs_sys_t0.count(), 1)
        self.assertTrue(hasattr(qs_sys_t0.first(), "meta_history_id"), "Should return MetaHistory")
        self.assertEqual(qs_sys_t0.first().name, "Original")

        qs_sys_t1 = get_queryset_as_of(self.HistoryModel, T1 + timedelta(minutes=1))
        self.assertEqual(qs_sys_t1.count(), 2)


# ====================================================================
#  From test_bitemporal_history_edit.py — Data-field edits on history
# ====================================================================

class BitemporalHistoryEditTest(_BitemporalDebugMixin, TransactionTestCase):
    """Test data-field edits on Level-1 history records."""

    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration

        mr = ModelRegistration()
        try:
            mr._register_standard_model(HistEditModel, [])
        except Exception:
            pass

        self.HistoryModel = HistEditModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        with connection.schema_editor() as editor:
            editor.create_model(HistEditModel)
            editor.create_model(self.HistoryModel)
            editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as editor:
            for m in (self.MetaModel, self.HistoryModel, HistEditModel):
                try:
                    editor.delete_model(m)
                except Exception:
                    pass

    def test_edit_current_history_updates_main_table(self):
        """
        If the currently-valid history record's name is changed directly
        on the H-table, the main table must reflect the new name.
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        self._dump_state("After t0: Create 'Alpha'", HistEditModel, self.HistoryModel, self.MetaModel)

        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        self.assertIsNone(h_alpha.valid_to)

        with patch("django.utils.timezone.now", return_value=t1):
            h_alpha.name = "Alpha-Fixed"
            h_alpha.save()

        self._dump_state("After t1: Edit h-record name → 'Alpha-Fixed'", HistEditModel, self.HistoryModel, self.MetaModel)

        obj.refresh_from_db()
        self.assertEqual(
            obj.name,
            "Alpha-Fixed",
            "Main table must be updated when the currently-valid history "
            "record's data field is edited.",
        )

    def test_edit_non_current_history_does_not_change_main_table(self):
        """
        If a *closed* (expired) history record is edited, the main table
        must stay unchanged.
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        self._dump_state("After t1: Update → 'Beta'", HistEditModel, self.HistoryModel, self.MetaModel)

        obj.refresh_from_db()
        self.assertEqual(obj.name, "Beta")

        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        self.assertIsNotNone(h_alpha.valid_to)

        with patch("django.utils.timezone.now", return_value=t2):
            h_alpha.name = "Alpha-Corrected"
            h_alpha.save()

        self._dump_state("After t2: Edit expired h_alpha → 'Alpha-Corrected'", HistEditModel, self.HistoryModel, self.MetaModel)

        obj.refresh_from_db()
        self.assertEqual(
            obj.name,
            "Beta",
            "Main table must NOT change when an expired history record is edited.",
        )

    def test_system_time_as_of_after_edit(self):
        """
        After editing a history record's name at t2, a system-time query
        before t2 must return the OLD name and after t2 the NEW name.
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t_pre_edit = t1 + timedelta(minutes=5)
        t_post_edit = t2 + timedelta(minutes=5)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="Alpha")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        h_beta = self.HistoryModel.objects.get(name="Beta")

        meta_count_before = self.MetaModel.objects.count()

        with patch("django.utils.timezone.now", return_value=t2):
            h_beta.name = "Beta-Fixed"
            h_beta.save()

        meta_count_after = self.MetaModel.objects.count()

        self.assertGreater(
            meta_count_after,
            meta_count_before,
            "Editing a history record's data fields must create a new meta-history version.",
        )

        qs_pre = get_queryset_as_of(self.HistoryModel, t_pre_edit)
        names_pre = sorted([m.name for m in qs_pre])
        self.assertIn("Beta", names_pre)
        self.assertNotIn("Beta-Fixed", names_pre)

        qs_post = get_queryset_as_of(self.HistoryModel, t_post_edit)
        names_post = sorted([m.name for m in qs_post])
        self.assertIn("Beta-Fixed", names_post)
        self.assertNotIn("Beta", names_post)

    def test_multiple_edits_produce_meta_versions(self):
        """
        Each data-field edit on the same history row must produce a
        distinct meta-history version.
        """
        t0 = datetime.datetime(2025, 7, 1, 10, 0, 0)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistEditModel.objects.create(name="V1")

        h = self.HistoryModel.objects.get(name="V1")

        for i, (name, minutes) in enumerate([
            ("V2", 10), ("V3", 20), ("V4", 30)
        ], start=1):
            t = t0 + timedelta(minutes=minutes)
            with patch("django.utils.timezone.now", return_value=t):
                h.name = name
                h.save()
            h.refresh_from_db()

        meta_versions = list(
            self.MetaModel.objects.filter(
                history_object=h,
            ).order_by("sys_from", "meta_history_id")
        )

        self.assertGreaterEqual(len(meta_versions), 4)

        meta_names = [m.name for m in meta_versions]
        self.assertEqual(meta_names, ["V1", "V2", "V3", "V4"])

        for m in meta_versions[:-1]:
            self.assertIsNotNone(m.sys_to)
        self.assertIsNone(meta_versions[-1].sys_to)


# ====================================================================
#  From test_bitemporal_history_deletion_as_of.py — History deletion
# ====================================================================

class BitemporalHistoryDeletionAsOfTest(_BitemporalDebugMixin, TransactionTestCase):
    """Reproduce and verify the history-deletion / system-time as_of bug."""

    def setUp(self):
        from lex.process_admin.utils.model_registration import ModelRegistration

        mr = ModelRegistration()
        try:
            mr._register_standard_model(HistDelAsOfModel, [])
        except Exception:
            pass

        self.HistoryModel = HistDelAsOfModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        with connection.schema_editor() as editor:
            editor.create_model(HistDelAsOfModel)
            editor.create_model(self.HistoryModel)
            editor.create_model(self.MetaModel)

    def tearDown(self):
        with connection.schema_editor() as editor:
            for m in (self.MetaModel, self.HistoryModel, HistDelAsOfModel):
                try:
                    editor.delete_model(m)
                except Exception:
                    pass

    def test_system_time_as_of_after_history_deletion(self):
        """
        After deleting a Level-1 history row at t3, a system-time query
        for any moment *before* t3 must return the pre-deletion snapshot.
        """
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)
        t_between_t2_t3 = t2 + timedelta(minutes=5)
        t_after_t3 = t3 + timedelta(minutes=5)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")
        self._dump_state("After t0: Create 'Test'", HistDelAsOfModel, self.HistoryModel, self.MetaModel)

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()
        self._dump_state("After t1: Modify → 'Test1'", HistDelAsOfModel, self.HistoryModel, self.MetaModel)

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()
        self._dump_state("After t2: Modify → 'Test2'", HistDelAsOfModel, self.HistoryModel, self.MetaModel)

        h_test = self.HistoryModel.objects.get(name="Test")
        h_test1 = self.HistoryModel.objects.get(name="Test1")
        h_test2 = self.HistoryModel.objects.get(name="Test2")

        self.assertEqual(h_test.valid_from, t0)
        self.assertEqual(h_test.valid_to, t1)
        self.assertEqual(h_test1.valid_from, t1)
        self.assertEqual(h_test1.valid_to, t2)
        self.assertEqual(h_test2.valid_from, t2)
        self.assertIsNone(h_test2.valid_to)

        h_test1_id = h_test1.history_id
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()
        self._dump_state("After t3: Delete history of 'Test1'", HistDelAsOfModel, self.HistoryModel, self.MetaModel)

        h_test.refresh_from_db()
        self.assertEqual(h_test.valid_to, t2)

        h_test2.refresh_from_db()
        self.assertIsNone(h_test2.valid_to)

        deletion_meta = self.MetaModel.objects.filter(
            history_object_id=h_test1_id,
            meta_history_type="-",
        )
        self.assertTrue(
            deletion_meta.exists(),
            "A '-' (deleted) meta-history record must be created when a history row is deleted.",
        )

        qs_before = get_queryset_as_of(self.HistoryModel, t_between_t2_t3)
        names_before = sorted(qs_before.values_list("name", flat=True))

        self.assertIn("Test", names_before)
        self.assertIn("Test1", names_before)
        self.assertIn("Test2", names_before)
        self.assertEqual(len(names_before), 3)

        qs_after = get_queryset_as_of(self.HistoryModel, t_after_t3)
        names_after = sorted(qs_after.values_list("name", flat=True))

        self.assertNotIn("Test1", names_after)
        self.assertEqual(len(names_after), 2)
        self.assertIn("Test", names_after)
        self.assertIn("Test2", names_after)

    def test_history_deletion_creates_meta_record(self):
        """Deleting a Level-1 history row must produce a '-' meta-history record."""
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t_delete = t0 + timedelta(minutes=20)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Alpha")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Beta"
            obj.save()

        h_alpha = self.HistoryModel.objects.get(name="Alpha")
        h_alpha_id = h_alpha.history_id

        with patch("django.utils.timezone.now", return_value=t_delete):
            h_alpha.delete()

        deletion_metas = self.MetaModel.objects.filter(
            history_object_id=h_alpha_id,
            meta_history_type="-",
        )
        self.assertTrue(
            deletion_metas.exists(),
            f"Expected a '-' meta-history record for deleted history row (history_id={h_alpha_id}).",
        )

        del_meta = deletion_metas.first()
        self.assertAlmostEqual(
            del_meta.sys_from.timestamp(),
            t_delete.timestamp(),
            delta=2,
        )

    def test_chain_repair_creates_new_meta_version(self):
        """When chain repair changes h_prev.valid_to, a new meta-history version must be created."""
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t_delete = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="A")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "B"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "C"
            obj.save()

        h_a = self.HistoryModel.objects.get(name="A")
        h_b = self.HistoryModel.objects.get(name="B")

        self.assertEqual(h_a.valid_to, t1)

        with patch("django.utils.timezone.now", return_value=t_delete):
            h_b.delete()

        h_a.refresh_from_db()
        self.assertEqual(h_a.valid_to, t2, "Chain repair should extend A's valid_to to t2")

        meta_a_after = list(
            self.MetaModel.objects.filter(history_object=h_a)
            .order_by("sys_from", "meta_history_id")
        )

        has_repaired_version = any(m.valid_to == t2 for m in meta_a_after)
        self.assertTrue(has_repaired_version)

        old_versions = [m for m in meta_a_after if m.valid_to == t1]
        for old_v in old_versions:
            self.assertIsNotNone(old_v.sys_to)

    def test_valid_time_as_of_correct_after_history_deletion(self):
        """Valid-time queries on the main model still work after deletion + chain repair."""
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()

        h_test1 = self.HistoryModel.objects.get(name="Test1")
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()

        qs_t0 = get_queryset_as_of(HistDelAsOfModel, t0 + timedelta(minutes=5))
        self.assertEqual(qs_t0.count(), 1)
        self.assertEqual(qs_t0.first().name, "Test")

        qs_t1 = get_queryset_as_of(HistDelAsOfModel, t1 + timedelta(minutes=5))
        self.assertEqual(qs_t1.count(), 1)
        self.assertEqual(qs_t1.first().name, "Test")

        qs_t2 = get_queryset_as_of(HistDelAsOfModel, t2 + timedelta(minutes=5))
        self.assertEqual(qs_t2.count(), 1)
        self.assertEqual(qs_t2.first().name, "Test2")

    def test_system_time_shows_pre_and_post_deletion_snapshots(self):
        """System-time queries at different moments show different snapshots."""
        t0 = datetime.datetime(2025, 6, 1, 10, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t1 + timedelta(minutes=10)
        t3 = t2 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = HistDelAsOfModel.objects.create(name="Test")

        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "Test1"
            obj.save()

        with patch("django.utils.timezone.now", return_value=t2):
            obj.name = "Test2"
            obj.save()

        qs_pre = get_queryset_as_of(self.HistoryModel, t2 + timedelta(minutes=5))
        names_pre = sorted(qs_pre.values_list("name", flat=True))
        self.assertEqual(len(names_pre), 3)

        h_test1 = self.HistoryModel.objects.get(name="Test1")
        with patch("django.utils.timezone.now", return_value=t3):
            h_test1.delete()

        self._dump_state("After deletion at t3", HistDelAsOfModel, self.HistoryModel, self.MetaModel)

        qs_pre2 = get_queryset_as_of(self.HistoryModel, t2 + timedelta(minutes=5))
        names_pre2 = sorted(qs_pre2.values_list("name", flat=True))
        self.assertIn("Test1", names_pre2)
        self.assertEqual(len(names_pre2), 3)

        qs_post = get_queryset_as_of(self.HistoryModel, t3 + timedelta(minutes=5))
        names_post = sorted(qs_post.values_list("name", flat=True))
        self.assertNotIn("Test1", names_post)
        self.assertEqual(len(names_post), 2)
        self.assertIn("Test", names_post)
        self.assertIn("Test2", names_post)

        test_meta_post = [r for r in qs_post if r.name == "Test"]
        self.assertEqual(len(test_meta_post), 1)
        self.assertEqual(test_meta_post[0].valid_to, t2)


# ====================================================================
#  From test_history_api.py — REST API history endpoint
# ====================================================================

class TestHistoryTimelineAPI(TransactionTestCase):
    """Prove history timeline endpoint semantics and as-of query correctness."""

    def setUp(self):
        from django.contrib.auth.models import User
        from rest_framework.test import APIClient
        from lex.tests.integration.test_event_scheduling import SchedTestModel
        from simple_history.models import registered_models

        self.SchedTestModel = SchedTestModel

        # Create User
        self.user = User.objects.create_user(username='testuser', password='password', email='test@example.com')
        self.client = APIClient()
        self.client.force_login(self.user)

        # Inject OIDC session data
        session = self.client.session
        session['oidc_expires_at'] = (timezone.now() + timedelta(hours=1)).timestamp()
        session.save()

        # Register model
        if SchedTestModel not in registered_models:
            from lex.process_admin.utils.model_registration import ModelRegistration
            ModelRegistration._register_standard_model(SchedTestModel, [])

        self.HistoryModel = SchedTestModel.history.model
        self.MetaModel = self.HistoryModel.meta_history.model

        # Create Tables
        with connection.schema_editor() as schema_editor:
            tables = connection.introspection.table_names()
            if SchedTestModel._meta.db_table not in tables:
                schema_editor.create_model(SchedTestModel)
            if self.HistoryModel._meta.db_table not in tables:
                schema_editor.create_model(self.HistoryModel)
            if self.MetaModel._meta.db_table not in tables:
                schema_editor.create_model(self.MetaModel)

        self.obj = SchedTestModel.objects.create(name="Version 1")

        # URL rebuild
        from django.urls import converters as django_converters
        from django.urls.converters import REGISTERED_CONVERTERS

        real_register_converter = django_converters.register_converter

        def idempotent_register_converter(converter, type_name):
            REGISTERED_CONVERTERS.pop(type_name, None)
            return real_register_converter(converter, type_name)

        converter_patch = patch(
            "lex.process_admin.sites.process_admin_site.register_converter",
            new=idempotent_register_converter,
        )
        converter_patch.start()
        try:
            from lex.process_admin.settings import processAdminSite
            processAdminSite.initialized = False
            _ = processAdminSite.urls
            from django.urls import clear_url_caches
            from django.conf import settings
            import sys
            clear_url_caches()
            if settings.ROOT_URLCONF in sys.modules:
                reload(sys.modules[settings.ROOT_URLCONF])
        finally:
            converter_patch.stop()

    def tearDown(self):
        with connection.schema_editor() as schema_editor:
            try:
                schema_editor.delete_model(self.MetaModel)
            except Exception:
                pass
            try:
                schema_editor.delete_model(self.HistoryModel)
            except Exception:
                pass
            try:
                schema_editor.delete_model(self.SchedTestModel)
            except Exception:
                pass

    def test_get_history_deleted_record(self):
        """Retrieving history for a deleted record returns a '-' entry."""
        from django.urls import reverse
        from rest_framework import status as drf_status

        self.obj.name = "Pre-Delete Version"
        self.obj.save()
        pk = self.obj.pk
        self.obj.delete()

        class MockContainer:
            id = 'schedtestmodel'

        url = reverse(
            'process_admin_rest_api:model-history-list',
            kwargs={'model_container': MockContainer(), 'calculationId': 'default', 'pk': pk}
        )

        response = self.client.get(url)
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK)
        data = response.json()
        self.assertEqual(data[0]['history_type'], '-')

    def test_modify_initial_history_valid_from(self):
        """Modifying valid_from of the initial history record persists correctly."""
        t0 = timezone.now()
        new_obj = self.SchedTestModel.objects.create(name="Fresh Object")

        self.assertEqual(new_obj.history.count(), 1)
        initial_history = new_obj.history.first()

        new_valid_from = t0 - timedelta(hours=1)
        initial_history.valid_from = new_valid_from
        initial_history.save()

        refetched_history = new_obj.history.filter(pk=initial_history.pk).first()
        self.assertEqual(refetched_history.valid_from, new_valid_from)

    def test_list_as_of_after_history_valid_from_correction(self):
        """
        After moving an update's valid_from forward via history row edit,
        the list as_of at the original time shows the pre-update value.
        """
        from django.urls import reverse
        from rest_framework import status as drf_status
        from lex.core.models.LexModel import PermissionResult

        t_initial = datetime.datetime(2025, 1, 1, 11, 50, 0)
        t_update = datetime.datetime(2025, 1, 1, 12, 0, 0)
        t_valid_from_correction = datetime.datetime(2025, 1, 1, 12, 5, 0)
        t_history_edit = datetime.datetime(2025, 1, 1, 12, 8, 0)
        t_as_of = datetime.datetime(2025, 1, 1, 11, 55, 0)

        with patch('django.utils.timezone.now', return_value=t_initial):
            target = self.SchedTestModel.objects.create(name="Before Update")

        with patch('django.utils.timezone.now', return_value=t_update):
            target.name = "After Update"
            target.save()

        with patch('django.utils.timezone.now', return_value=t_history_edit):
            updated_history = (
                target.history.filter(name="After Update")
                .order_by("-history_id")
                .first()
            )
            self.assertIsNotNone(updated_history)
            updated_history.valid_from = t_valid_from_correction
            updated_history.save()

        qs = get_queryset_as_of(self.SchedTestModel, t_as_of)
        self.assertEqual(qs.filter(id=target.id).count(), 1)

        class MockContainer:
            id = 'schedtestmodel'

        url = reverse(
            'process_admin_rest_api:model-entries-list',
            kwargs={'model_container': MockContainer()},
        )

        with patch('lex.api.views.model_entries.List.ListModelEntries.filter_backends', []), patch.object(
            self.SchedTestModel,
            'permission_read',
            new=lambda _self, _user_context: PermissionResult.allow_all("test"),
        ):
            response = self.client.get(url, {'as_of': t_as_of.isoformat()})
        self.assertEqual(response.status_code, drf_status.HTTP_200_OK, response.content)

        payload = response.json()
        results = payload.get("results", payload) if isinstance(payload, dict) else payload
        row = next((entry for entry in results if entry.get("id") == target.id), None)
        self.assertIsNotNone(row, f"Target row {target.id} not found in as_of payload: {payload}")
        self.assertEqual(row.get("name"), "Before Update")
