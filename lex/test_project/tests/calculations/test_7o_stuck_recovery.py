"""Cluster 7o: operator visibility and stuck-calculation recovery.

Intent: operators need a safe public way to see which calculations are still
running, identify the ones that are stuck, and bulk-cancel wedged Celery work
without inventing a parallel registry. Regressions here either hide live work
from the operator dashboard or make the recovery sweep lie about what it
cancelled.
Cluster 7o — scenarios 7.176–7.184. Type: U.
Covers: lex/core/models/CalculationModel.py, lex/core/signals/ActiveCalculationStateStore.py.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7o_stuck_recovery.py -v
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

pytestmark = pytest.mark.calculations


class TestCluster07o_StateStoreAges(SimpleTestCase):
    """Cluster 7o: age tracking and active-entry listing."""

    def setUp(self):
        ActiveCalculationStateStore.clear_all()

    def tearDown(self):
        ActiveCalculationStateStore.clear_all()

    def test_07_176_mark_in_progress_stamps_started_at_metadata_on_first_registration(self):
        """
        Scenario 7.176: first registration captures visible start metadata.
        Given: a record entering IN_PROGRESS for the first time.
        When:  mark_in_progress() registers it.
        Then:  the store keeps both a monotonic start reading for age math
               and a UTC ISO timestamp for operator display.
        """
        iso_now = MagicMock()
        iso_now.isoformat.return_value = "2026-06-02T10:45:00+00:00"
        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=123.45,
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value = iso_now
            ActiveCalculationStateStore.mark_in_progress(
                record_id="atomiccalc_1",
                calculation_id="calc-1",
                record="AtomicCalc #1",
                model_label="lex_app.atomiccalc",
                record_pk=1,
            )

        entry = ActiveCalculationStateStore.get_entry("atomiccalc_1")
        self.assertEqual(
            entry["started_at_monotonic"],
            123.45,
            msg="first registration must preserve the monotonic start timestamp",
        )
        self.assertEqual(
            entry["started_at_iso"],
            "2026-06-02T10:45:00+00:00",
            msg="first registration must preserve the UTC ISO display timestamp",
        )

    def test_07_177_reentrant_registration_preserves_original_start_time_and_task_id(self):
        """
        Scenario 7.177: re-entry must not reset the visible running age.
        Given: a tracked record that already has start metadata and a task_id.
        When:  mark_in_progress() is called again for the same record_id.
        Then:  the original start metadata and task_id stay intact.
        """
        first_iso = MagicMock()
        first_iso.isoformat.return_value = "2026-06-02T10:00:00+00:00"
        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=100.0,
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value = first_iso
            ActiveCalculationStateStore.mark_in_progress(
                record_id="atomiccalc_1",
                calculation_id="calc-1",
                record="AtomicCalc #1",
                model_label="lex_app.atomiccalc",
                record_pk=1,
            )
        ActiveCalculationStateStore.set_task_id("atomiccalc_1", "task-123")

        second_iso = MagicMock()
        second_iso.isoformat.return_value = "2026-06-02T11:00:00+00:00"
        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=999.0,
        ), patch(
            "lex.core.signals.ActiveCalculationStateStore.datetime"
        ) as mocked_datetime:
            mocked_datetime.now.return_value = second_iso
            ActiveCalculationStateStore.mark_in_progress(
                record_id="atomiccalc_1",
                calculation_id="calc-1",
                record="AtomicCalc #1 (re-entered)",
                model_label="lex_app.atomiccalc",
                record_pk=1,
            )

        entry = ActiveCalculationStateStore.get_entry("atomiccalc_1")
        self.assertEqual(
            entry["started_at_monotonic"],
            100.0,
            msg="re-entrant registration must keep the original monotonic start time",
        )
        self.assertEqual(
            entry["started_at_iso"],
            "2026-06-02T10:00:00+00:00",
            msg="re-entrant registration must keep the original display timestamp",
        )
        self.assertEqual(
            entry["task_id"],
            "task-123",
            msg="re-entrant registration must not orphan the existing Celery task id",
        )

    def test_07_178_list_active_returns_every_entry_oldest_first_with_computed_age(self):
        """
        Scenario 7.178: operator listing orders oldest work first.
        Given: three active entries with different monotonic start times.
        When:  list_active() is called without an age filter.
        Then:  every entry is returned oldest-first with a computed age_seconds.
        """
        ActiveCalculationStateStore._state_map = {
            "old_1": {
                "record_id": "old_1",
                "record": "Old",
                "calculation_id": "calc-old",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "1",
                "task_id": "task-old",
                "started_at_monotonic": 100.0,
                "started_at_iso": "2026-06-02T10:00:00+00:00",
            },
            "mid_1": {
                "record_id": "mid_1",
                "record": "Mid",
                "calculation_id": "calc-mid",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "2",
                "task_id": "task-mid",
                "started_at_monotonic": 150.0,
                "started_at_iso": "2026-06-02T10:10:00+00:00",
            },
            "new_1": {
                "record_id": "new_1",
                "record": "New",
                "calculation_id": "calc-new",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "3",
                "task_id": "",
                "started_at_monotonic": 190.0,
                "started_at_iso": "2026-06-02T10:19:00+00:00",
            },
        }

        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=200.0,
        ):
            entries = ActiveCalculationStateStore.list_active()

        self.assertEqual(
            [entry["record_id"] for entry in entries],
            ["old_1", "mid_1", "new_1"],
            msg="list_active() must sort entries oldest-first for operator triage",
        )
        self.assertEqual(
            [entry["age_seconds"] for entry in entries],
            [100.0, 50.0, 10.0],
            msg="list_active() must expose computed ages for every active entry",
        )

    def test_07_179_list_active_filters_inclusively_and_zero_keeps_everything(self):
        """
        Scenario 7.179: age filtering is inclusive and 0 is the full-list boundary.
        Given: active entries aged 100s, 50s, and 10s.
        When:  list_active() is called with a threshold.
        Then:  entries with age >= threshold remain, and 0 returns everything.
        """
        ActiveCalculationStateStore._state_map = {
            "old_1": {
                "record_id": "old_1",
                "record": "Old",
                "calculation_id": "calc-old",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "1",
                "task_id": "task-old",
                "started_at_monotonic": 100.0,
                "started_at_iso": "2026-06-02T10:00:00+00:00",
            },
            "mid_1": {
                "record_id": "mid_1",
                "record": "Mid",
                "calculation_id": "calc-mid",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "2",
                "task_id": "task-mid",
                "started_at_monotonic": 150.0,
                "started_at_iso": "2026-06-02T10:10:00+00:00",
            },
            "new_1": {
                "record_id": "new_1",
                "record": "New",
                "calculation_id": "calc-new",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "3",
                "task_id": "",
                "started_at_monotonic": 190.0,
                "started_at_iso": "2026-06-02T10:19:00+00:00",
            },
        }

        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=200.0,
        ):
            filtered = ActiveCalculationStateStore.list_active(older_than_seconds=50.0)
            zero_boundary = ActiveCalculationStateStore.list_active(older_than_seconds=0.0)

        self.assertEqual(
            [entry["record_id"] for entry in filtered],
            ["old_1", "mid_1"],
            msg="age filtering must be inclusive so an exact-threshold entry still appears",
        )
        self.assertEqual(
            len(zero_boundary),
            3,
            msg="threshold 0 must return every active entry, not an empty list",
        )

    def test_07_180_list_active_rejects_negative_thresholds_and_handles_legacy_entries(self):
        """
        Scenario 7.180: invalid thresholds fail fast and legacy entries stay readable.
        Given: a negative threshold and a legacy entry written before age tracking existed.
        When:  list_active() is called.
        Then:  negative thresholds raise ValueError, and legacy entries report age 0.0.
        """
        with self.assertRaisesRegex(
            ValueError,
            "older_than_seconds must be >= 0",
        ):
            ActiveCalculationStateStore.list_active(older_than_seconds=-1.0)

        ActiveCalculationStateStore._state_map = {
            "legacy_1": {
                "record_id": "legacy_1",
                "record": "Legacy",
                "calculation_id": "calc-legacy",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "1",
                "task_id": "",
            }
        }
        with patch(
            "lex.core.signals.ActiveCalculationStateStore.time.monotonic",
            return_value=250.0,
        ):
            entries = ActiveCalculationStateStore.list_active()

        self.assertEqual(
            entries[0]["age_seconds"],
            0.0,
            msg="legacy store entries must degrade to age 0.0 instead of crashing the operator list",
        )


class TestCluster07o_OperatorRecovery(SimpleTestCase):
    """Cluster 7o: public report projection and stuck-recovery API."""

    def test_07_181_list_in_progress_projects_public_shape_and_cancellable_flag(self):
        """
        Scenario 7.181: the public in-progress report hides internal bookkeeping.
        Given: raw state-store entries with task metadata and internal timing fields.
        When:  CalculationModel.list_in_progress() is called.
        Then:  callers get the public report shape with started_at and cancellable.
        """
        raw_entries = [
            {
                "record_id": "atomiccalc_1",
                "record": "AtomicCalc #1",
                "model_label": "lex_app.atomiccalc",
                "calculation_id": "calc-1",
                "record_pk": "1",
                "task_id": "task-123",
                "started_at_iso": "2026-06-02T10:00:00+00:00",
                "started_at_monotonic": 100.0,
                "age_seconds": 600.0,
            },
            {
                "record_id": "atomiccalc_2",
                "record": "AtomicCalc #2",
                "model_label": "lex_app.atomiccalc",
                "calculation_id": "calc-2",
                "record_pk": "2",
                "task_id": "",
                "started_at_iso": "2026-06-02T10:05:00+00:00",
                "started_at_monotonic": 110.0,
                "age_seconds": 300.0,
            },
        ]

        with patch.object(
            ActiveCalculationStateStore,
            "list_active",
            return_value=raw_entries,
        ):
            report = CalculationModel.list_in_progress()

        self.assertEqual(
            report,
            [
                {
                    "record_id": "atomiccalc_1",
                    "record": "AtomicCalc #1",
                    "model_label": "lex_app.atomiccalc",
                    "calculation_id": "calc-1",
                    "task_id": "task-123",
                    "started_at": "2026-06-02T10:00:00+00:00",
                    "age_seconds": 600.0,
                    "cancellable": True,
                },
                {
                    "record_id": "atomiccalc_2",
                    "record": "AtomicCalc #2",
                    "model_label": "lex_app.atomiccalc",
                    "calculation_id": "calc-2",
                    "task_id": None,
                    "started_at": "2026-06-02T10:05:00+00:00",
                    "age_seconds": 300.0,
                    "cancellable": False,
                },
            ],
            msg="list_in_progress() must expose only the public report fields and the cancellable flag",
        )

    def test_07_182_find_stuck_delegates_age_filtering_and_reuses_public_projection(self):
        """
        Scenario 7.182: stuck lookup is just the age-filtered public report.
        Given: the state store's list_active() surface.
        When:  CalculationModel.find_stuck(older_than_seconds) is called.
        Then:  it delegates the threshold to the store and returns the same public shape.
        """
        raw_entries = [
            {
                "record_id": "atomiccalc_1",
                "record": "AtomicCalc #1",
                "model_label": "lex_app.atomiccalc",
                "calculation_id": "calc-1",
                "record_pk": "1",
                "task_id": "task-123",
                "started_at_iso": "2026-06-02T10:00:00+00:00",
                "age_seconds": 601.0,
            }
        ]

        with patch.object(
            ActiveCalculationStateStore,
            "list_active",
            return_value=raw_entries,
        ) as list_active_mock:
            report = CalculationModel.find_stuck(600.0)

        list_active_mock.assert_called_once_with(older_than_seconds=600.0)
        self.assertEqual(
            report,
            [
                {
                    "record_id": "atomiccalc_1",
                    "record": "AtomicCalc #1",
                    "model_label": "lex_app.atomiccalc",
                    "calculation_id": "calc-1",
                    "task_id": "task-123",
                    "started_at": "2026-06-02T10:00:00+00:00",
                    "age_seconds": 601.0,
                    "cancellable": True,
                }
            ],
            msg="find_stuck() must return the same public report shape as list_in_progress()",
        )

    def test_07_183_cancel_stuck_reports_unresolvable_entries_as_errors(self):
        """
        Scenario 7.183: bulk recovery must report entries it cannot resolve.
        Given: a stuck-entry report item with no resolvable model identity.
        When:  cancel_stuck() processes it.
        Then:  the result is counted as an error with a clear detail string.
        """
        with patch.object(
            CalculationModel,
            "find_stuck",
            return_value=[
                {
                    "record_id": "broken_1",
                    "record": "Broken",
                    "model_label": "",
                    "calculation_id": "",
                    "task_id": "task-broken",
                    "started_at": "2026-06-02T10:00:00+00:00",
                    "age_seconds": 999.0,
                    "cancellable": True,
                }
            ],
        ):
            report = CalculationModel.cancel_stuck(600.0)

        self.assertEqual(
            report["threshold_seconds"],
            600.0,
            msg="cancel_stuck() must echo the threshold as a float in its summary",
        )
        self.assertEqual(
            report["errors"],
            1,
            msg="an unresolvable stuck entry must increment the bulk-recovery error counter",
        )
        self.assertEqual(
            report["results"][0],
            {
                "record_id": "broken_1",
                "outcome": "error",
                "status": None,
                "detail": "could_not_resolve_instance",
            },
            msg="unresolvable entries must come back as explicit error rows, not silent drops",
        )

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "BUG-023: cancel_stuck() reuses find_stuck()'s public projection, which strips "
            "record_pk before _resolve_instance_for_entry() runs, so resolvable stuck rows "
            "are all reported as could_not_resolve_instance."
        ),
    )
    def test_07_184_cancel_stuck_reuses_cancel_and_collapses_descendants(self):
        """
        Scenario 7.184: bulk recovery cancels parents once and skips collapsed descendants.
        Given: one cancellable parent, its descendant sharing the same calculation_id,
               and one sync calculation with no task_id.
        When:  cancel_stuck() runs over the public stuck surface.
        Then:  the parent is cancelled once, the descendant is collapsed under it,
               the sync calculation is reported as skipped, and there are no false errors.
        """
        raw_entries = [
            {
                "record_id": "parentcalc_1",
                "record": "Parent #1",
                "model_label": "lex_app.parentcalc",
                "record_pk": "1",
                "calculation_id": "calc-tree",
                "task_id": "task-parent",
                "started_at_iso": "2026-06-02T10:00:00+00:00",
                "age_seconds": 900.0,
            },
            {
                "record_id": "childcalc_2",
                "record": "Child #2",
                "model_label": "lex_app.childcalc",
                "record_pk": "2",
                "calculation_id": "calc-tree",
                "task_id": "task-child",
                "started_at_iso": "2026-06-02T10:01:00+00:00",
                "age_seconds": 850.0,
            },
            {
                "record_id": "atomiccalc_3",
                "record": "Atomic #3",
                "model_label": "lex_app.atomiccalc",
                "record_pk": "3",
                "calculation_id": "calc-sync",
                "task_id": "",
                "started_at_iso": "2026-06-02T10:02:00+00:00",
                "age_seconds": 700.0,
            },
        ]
        instances = {
            "parentcalc_1": SimpleNamespace(name="parent"),
            "atomiccalc_3": SimpleNamespace(name="sync"),
        }

        def resolve_side_effect(entry, apps):
            if not entry.get("record_pk"):
                return None
            return instances.get(entry["record_id"])

        with patch.object(
            ActiveCalculationStateStore,
            "list_active",
            return_value=raw_entries,
        ), patch.object(
            CalculationModel,
            "_resolve_instance_for_entry",
            side_effect=resolve_side_effect,
        ), patch.object(
            CalculationModel,
            "cancel",
            side_effect=[
                {
                    "cancelled": True,
                    "status": CalculationModel.CANCELLED,
                    "reason": "",
                },
                {
                    "cancelled": False,
                    "status": CalculationModel.IN_PROGRESS,
                    "reason": "sync_calculation_not_cancellable",
                },
            ],
        ) as cancel_mock:
            report = CalculationModel.cancel_stuck(600.0, reason="operator recovery")

        cancel_mock.assert_has_calls(
            [
                call(instances["parentcalc_1"], recursive=True, reason="operator recovery"),
                call(instances["atomiccalc_3"], recursive=True, reason="operator recovery"),
            ]
        )
        self.assertEqual(
            report["candidates"],
            3,
            msg="bulk recovery must count every stuck entry before collapsing descendants",
        )
        self.assertEqual(
            report["cancelled"],
            1,
            msg="the parent tree should count as one cancelled top-level recovery target",
        )
        self.assertEqual(
            report["skipped_not_cancellable"],
            1,
            msg="sync calculations must be reported as skipped, not as cancellation failures",
        )
        self.assertEqual(
            report["errors"],
            0,
            msg="resolvable entries must not be misreported as could_not_resolve_instance",
        )
        self.assertEqual(
            report["results"],
            [
                {
                    "record_id": "parentcalc_1",
                    "outcome": "cancelled",
                    "status": CalculationModel.CANCELLED,
                    "detail": "",
                },
                {
                    "record_id": "atomiccalc_3",
                    "outcome": "skipped",
                    "status": CalculationModel.IN_PROGRESS,
                    "detail": "sync_calculation_not_cancellable",
                },
            ],
            msg="bulk recovery must collapse descendants under their parent and keep the top-level summary accurate",
        )
