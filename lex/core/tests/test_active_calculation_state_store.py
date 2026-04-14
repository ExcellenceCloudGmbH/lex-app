"""
Unit tests for ``ActiveCalculationStateStore`` — the in-memory calculation tracker.

**What this tests (customer-visible behaviour)**

The store tracks which records currently have an active (IN_PROGRESS)
calculation.  WebSocket consumers read the ``snapshot()`` on (re)connect
to reconcile the frontend progress indicators.  If the store misses an
entry or returns stale data, the user sees a stuck spinner or loses the
progress badge on page refresh.

**Why it matters**

This was migrated from DatabaseCache to an in-memory store to fix a
critical bug where cache writes inside ``transaction.atomic()`` were
invisible to WebSocket handlers.  The in-memory store must be
thread-safe and provide correct snapshots at all times.

**Methodology**

``TestActiveCalculationStateStore`` and ``TestSplitRecordId`` cover
the pure-logic public API via ``SimpleTestCase`` (no DB access).
``TestValidateAndPrune`` and ``TestResolveModelAndPk`` exercise the
startup-prune path and internal model-resolution helpers via
``TransactionTestCase`` with a dynamically-created test table.

Run::

    lex test lex.core.tests.test_active_calculation_state_store --verbosity=2 --noinput --keepdb
"""

from unittest.mock import patch

from django.db import connection, models
from django.test import SimpleTestCase, TransactionTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore


class TestActiveCalculationStateStore(SimpleTestCase):
    """Prove the in-memory store tracks active calculations correctly."""

    def setUp(self):
        ActiveCalculationStateStore.clear_all()

    def tearDown(self):
        ActiveCalculationStateStore.clear_all()

    # ── mark_in_progress / clear ─────────────────────────────────────

    def test_mark_and_retrieve(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="calc_001",
            record="Period 42",
        )
        entry = ActiveCalculationStateStore.get_entry("period_42")
        self.assertEqual(entry["record_id"], "period_42")
        self.assertEqual(entry["calculation_id"], "calc_001")

    def test_clear_removes_entry(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="calc_001",
            record="Period 42",
        )
        ActiveCalculationStateStore.clear("period_42")
        entry = ActiveCalculationStateStore.get_entry("period_42")
        self.assertEqual(entry, {})

    def test_clear_nonexistent_is_noop(self):
        ActiveCalculationStateStore.clear("does_not_exist")

    def test_clear_all_empties_store(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="a_1", calculation_id="c1", record="A"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="b_2", calculation_id="c2", record="B"
        )
        ActiveCalculationStateStore.clear_all()
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    # ── get_calculation_id ───────────────────────────────────────────

    def test_get_calculation_id(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="calc_001",
            record="Period 42",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_calculation_id("period_42"),
            "calc_001",
        )

    def test_get_calculation_id_missing_returns_none(self):
        self.assertIsNone(ActiveCalculationStateStore.get_calculation_id("missing"))

    def test_get_calculation_id_empty_string_returns_none(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="",
            record="Period 42",
        )
        self.assertIsNone(ActiveCalculationStateStore.get_calculation_id("period_42"))

    # ── snapshot ─────────────────────────────────────────────────────

    def test_snapshot_returns_sorted_entries(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="z_1", calculation_id="c1", record="Z"
        )
        ActiveCalculationStateStore.mark_in_progress(
            record_id="a_1", calculation_id="c2", record="A"
        )
        snapshot = ActiveCalculationStateStore.snapshot()
        self.assertEqual(len(snapshot), 2)
        self.assertEqual(snapshot[0]["record_id"], "a_1")
        self.assertEqual(snapshot[1]["record_id"], "z_1")

    def test_snapshot_empty_store(self):
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    def test_snapshot_contains_expected_keys(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="nav_1",
            calculation_id="calc_x",
            record="NAV 1",
        )
        snapshot = ActiveCalculationStateStore.snapshot()
        entry = snapshot[0]
        self.assertIn("record_id", entry)
        self.assertIn("record", entry)
        self.assertIn("calculation_id", entry)

    # ── get_entry ────────────────────────────────────────────────────

    def test_get_entry_empty_record_id(self):
        self.assertEqual(ActiveCalculationStateStore.get_entry(""), {})

    def test_get_entry_returns_copy(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="calc_001",
            record="Period 42",
        )
        entry1 = ActiveCalculationStateStore.get_entry("period_42")
        entry2 = ActiveCalculationStateStore.get_entry("period_42")
        self.assertEqual(entry1, entry2)
        self.assertIsNot(entry1, entry2)

    # ── mark_in_progress edge cases ──────────────────────────────────

    def test_mark_empty_record_id_is_noop(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="",
            calculation_id="calc",
            record="X",
        )
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    def test_mark_with_model_label_and_pk(self):
        ActiveCalculationStateStore.mark_in_progress(
            record_id="period_42",
            calculation_id="calc",
            record="Period",
            model_label="core.period",
            record_pk=42,
        )
        entry = ActiveCalculationStateStore.get_entry("period_42")
        self.assertEqual(entry["model_label"], "core.period")
        self.assertEqual(entry["record_pk"], "42")


class TestSplitRecordId(SimpleTestCase):
    """Prove ``_split_record_id`` parses model_name and pk from record_id."""

    def test_standard_record_id(self):
        model_name, pk = ActiveCalculationStateStore._split_record_id("period_42")
        self.assertEqual(model_name, "period")
        self.assertEqual(pk, "42")

    def test_multi_underscore_record_id(self):
        model_name, pk = ActiveCalculationStateStore._split_record_id("my_model_name_42")
        self.assertEqual(model_name, "my_model_name")
        self.assertEqual(pk, "42")

    def test_no_underscore_returns_none(self):
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("nounderscore"),
            (None, None),
        )

    def test_empty_returns_none(self):
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id(""),
            (None, None),
        )

    def test_trailing_underscore_returns_none(self):
        """'model_' splits to ('model', '') which should return None."""
        self.assertEqual(
            ActiveCalculationStateStore._split_record_id("model_"),
            (None, None),
        )


# ── DB-dependent tests ──────────────────────────────────────────────────


class PruneTestCalcModel(CalculationModel):
    """Disposable CalculationModel for validate_and_prune tests."""

    name = models.CharField(max_length=100, default="")

    class Meta:
        app_label = "lex_app"

    def calculate(self):
        pass

    def permission_read(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("test")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True


class TestValidateAndPrune(TransactionTestCase):
    """
    Exercise ``validate_and_prune`` — the startup-only method that
    removes store entries whose DB row is no longer IN_PROGRESS.

    Why this matters: after an unclean server restart, stale entries
    can remain in the store (if the process was killed without clearing
    them). ``validate_and_prune`` is called during startup to reconcile
    the store against the database. If it fails silently, the frontend
    shows phantom spinners for calculations that already finished.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(PruneTestCalcModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(PruneTestCalcModel)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

        # PruneTestCalcModel is dynamically created via schema_editor so
        # it is NOT registered in the Django app registry.  Patch the
        # internal model-resolution helper so validate_and_prune can find it.
        _original_find = ActiveCalculationStateStore._find_model_by_name

        def _patched_find(name):
            if name == PruneTestCalcModel._meta.model_name:
                return PruneTestCalcModel
            return _original_find(name)

        find_patch = patch.object(
            ActiveCalculationStateStore,
            "_find_model_by_name",
            side_effect=_patched_find,
        )
        find_patch.start()
        self.addCleanup(find_patch.stop)

    def test_keeps_in_progress_entries(self):
        """Entries whose DB row is still IN_PROGRESS survive the prune."""
        # Use save(skip_hooks=True) to bypass calculate_hook — otherwise
        # the hook runs execute_calculation_sync which flips the status
        # to SUCCESS before we can test the prune.
        obj = PruneTestCalcModel(name="active")
        obj.is_calculated = CalculationModel.IN_PROGRESS
        obj.save(skip_hooks=True)

        record_id = f"{obj._meta.model_name}_{obj.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="c1",
            record=str(obj),
            model_label=obj._meta.label_lower,
            record_pk=obj.pk,
        )

        ActiveCalculationStateStore.validate_and_prune()

        self.assertNotEqual(
            ActiveCalculationStateStore.get_entry(record_id), {},
            "IN_PROGRESS entry should be kept after prune",
        )

    def test_removes_success_entries(self):
        """Entries whose DB row moved to SUCCESS are pruned."""
        obj = PruneTestCalcModel(name="done")
        obj.is_calculated = CalculationModel.SUCCESS
        obj.save(skip_hooks=True)
        record_id = f"{obj._meta.model_name}_{obj.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="c2",
            record=str(obj),
            model_label=obj._meta.label_lower,
            record_pk=obj.pk,
        )

        ActiveCalculationStateStore.validate_and_prune()

        self.assertEqual(
            ActiveCalculationStateStore.get_entry(record_id), {},
            "SUCCESS entry should be pruned",
        )

    def test_removes_entry_for_deleted_record(self):
        """Entries whose DB row no longer exists are pruned."""
        obj = PruneTestCalcModel(name="ghost")
        obj.is_calculated = CalculationModel.IN_PROGRESS
        obj.save(skip_hooks=True)
        record_id = f"{obj._meta.model_name}_{obj.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="c3",
            record=str(obj),
            model_label=obj._meta.label_lower,
            record_pk=obj.pk,
        )
        obj.delete()

        ActiveCalculationStateStore.validate_and_prune()

        self.assertEqual(
            ActiveCalculationStateStore.get_entry(record_id), {},
            "Entry for deleted record should be pruned",
        )

    def test_empty_store_is_noop(self):
        """validate_and_prune on an empty store returns without error."""
        ActiveCalculationStateStore.validate_and_prune()
        self.assertEqual(ActiveCalculationStateStore.snapshot(), [])

    def test_removes_entry_with_unresolvable_model(self):
        """
        Entries whose model_label cannot be resolved to an actual
        Django model are dropped (e.g. if the model was removed from
        the codebase between deploys).
        """
        ActiveCalculationStateStore.mark_in_progress(
            record_id="nosuchmodel_99",
            calculation_id="c4",
            record="phantom",
            model_label="nosuchapp.nosuchmodel",
            record_pk=99,
        )

        ActiveCalculationStateStore.validate_and_prune()

        self.assertEqual(
            ActiveCalculationStateStore.get_entry("nosuchmodel_99"), {},
            "Unresolvable model entry should be pruned",
        )


class TestResolveModelAndPk(TransactionTestCase):
    """
    Exercise ``_resolve_model_and_pk`` — the internal helper that maps
    a store entry dict back to a (model_class, pk) pair.

    Why this matters: if resolution fails silently (returns None, None),
    ``validate_and_prune`` drops the entry even though the calculation
    is still running. This causes phantom completion in the UI.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        with connection.schema_editor() as schema_editor:
            schema_editor.create_model(PruneTestCalcModel)

    @classmethod
    def tearDownClass(cls):
        try:
            with connection.schema_editor() as schema_editor:
                schema_editor.delete_model(PruneTestCalcModel)
        finally:
            super().tearDownClass()

    def setUp(self):
        super().setUp()
        # PruneTestCalcModel is dynamically created via schema_editor so
        # it is NOT registered in the Django app registry.  Patch the
        # internal model-resolution helper so _resolve_model_and_pk can find it.
        _original_find = ActiveCalculationStateStore._find_model_by_name

        def _patched_find(name):
            if name == PruneTestCalcModel._meta.model_name:
                return PruneTestCalcModel
            return _original_find(name)

        find_patch = patch.object(
            ActiveCalculationStateStore,
            "_find_model_by_name",
            side_effect=_patched_find,
        )
        find_patch.start()
        self.addCleanup(find_patch.stop)

    def test_resolves_via_model_label(self):
        """When model_label is present, resolves via apps.get_model."""
        entry = {
            "record_id": f"{PruneTestCalcModel._meta.model_name}_1",
            "model_label": PruneTestCalcModel._meta.label_lower,
            "record_pk": "1",
        }
        model_class, pk = ActiveCalculationStateStore._resolve_model_and_pk(entry)
        self.assertIs(model_class, PruneTestCalcModel)
        self.assertEqual(pk, "1")

    def test_falls_back_to_record_id_when_no_model_label(self):
        """When model_label is empty, derives model_name from record_id."""
        model_name = PruneTestCalcModel._meta.model_name
        entry = {
            "record_id": f"{model_name}_7",
            "model_label": "",
            "record_pk": "",
        }
        model_class, pk = ActiveCalculationStateStore._resolve_model_and_pk(entry)
        self.assertIs(model_class, PruneTestCalcModel)
        self.assertEqual(pk, "7")

    def test_returns_none_for_non_calculation_model(self):
        """LexModel subclasses that are NOT CalculationModel are rejected."""
        from django.contrib.auth.models import User

        entry = {
            "record_id": "user_1",
            "model_label": "auth.user",
            "record_pk": "1",
        }
        model_class, pk = ActiveCalculationStateStore._resolve_model_and_pk(entry)
        self.assertIsNone(model_class)
        self.assertIsNone(pk)

    def test_returns_none_for_empty_entry(self):
        """Completely empty entry returns (None, None)."""
        model_class, pk = ActiveCalculationStateStore._resolve_model_and_pk({})
        self.assertIsNone(model_class)
        self.assertIsNone(pk)

    def test_returns_none_for_nonexistent_app(self):
        """Bogus app_label falls through to _find_model_by_name."""
        entry = {
            "record_id": "foo_1",
            "model_label": "nonexistent_app.foo",
            "record_pk": "1",
        }
        model_class, pk = ActiveCalculationStateStore._resolve_model_and_pk(entry)
        # "foo" is not a registered CalculationModel
        self.assertIsNone(model_class)
