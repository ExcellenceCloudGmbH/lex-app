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

The public API is class-method based with no DB dependency (except
``validate_and_prune`` which we skip here).  Tests exercise the full
CRUD lifecycle and the ``_split_record_id`` helper.

Run::

    python manage.py test lex.core.tests.test_active_calculation_state_store
"""

from django.test import SimpleTestCase

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
