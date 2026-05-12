"""
Cluster 7e: Persistence internals + two-phase ``save()``.

Intent (from docs/features/calculations/ and the IN_PROGRESS-survives-
failure guarantee documented in cluster 7's overview):

    The :class:`CalculationModel` exposes a small set of bookkeeping
    helpers — ``_terminal_state_identity``, ``_mark_terminal_state_persisted``
    / ``_has_persisted_terminal_state`` / ``_clear_terminal_state_persistence``,
    ``_apply_in_progress_state_persistence`` / ``_register_in_progress_state_persistence``,
    and ``_queue_missing_in_progress_history`` — that exist for a single
    customer-visible reason: **the IN_PROGRESS row must survive a crash**.

    Without these helpers, a calculation that fails inside a
    ``transaction.atomic`` block would lose its IN_PROGRESS history row
    on rollback, the spinner in the UI would never clear, and the
    forensic trail used by support to diagnose the failure would be
    gone. The helpers are the safety net behind the documented
    two-phase save:

        Phase 1: persist the IN_PROGRESS row (inside a short atomic block).
        Phase 2: run ``calculate_hook`` *outside* that block, so even if
                 it raises, the IN_PROGRESS row is already committed and
                 visible to other clients.

    This sub-cluster pins both layers:

      * Scenarios 7.112 – 7.117 cover the helpers on their own (pure
        logic, no DB) — these are the safety-net invariants.
      * Scenarios 7.118 – 7.121 cover the observable two-phase contract
        through ``save()`` against a real DB row.

Scenario numbering matches
docs/test-plan/test-clusters.md#7e-persistence-internals--two-phase-save.
"""

from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.db import connection
from django.test import SimpleTestCase
from lex.api.utils import OperationContext
from lex.audit_logging.utils.ModelContext import model_logging_context
from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AtomicCalc,
    FailingCalc,
    PersistenceProbeCalc,
)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Helper-level safety-net tests (no DB needed — pure logic).
#  Even though these touch private API, the contracts they pin are
#  what keep the *public* two-phase guarantee honest.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster07e_TerminalStateIdentity(SimpleTestCase):
    """Scenario 7.112 — identity tuple used to deduplicate terminal-state writes."""

    # -- 7.112 ----------------------------------------------------------
    def test_7_112_identity_uses_class_and_pk_for_saved_rows(self) -> None:
        """Saved rows are deduplicated by ``(class, pk)`` — same identity for re-fetched rows."""
        obj = AtomicCalc(name="t7-15-saved")
        obj.pk = 7
        identity = CalculationModel._terminal_state_identity(obj)
        self.assertEqual(
            identity, (AtomicCalc, 7),
            "Identity must collapse to (class, pk) so two references to "
            "the same persisted row dedupe.",
        )

    def test_7_112_identity_falls_back_to_id_for_unsaved_rows(self) -> None:
        """Unsaved rows fall back to ``id(obj)`` so dedup still works in-memory."""
        obj = AtomicCalc(name="t7-15-unsaved")
        obj.pk = None
        identity = CalculationModel._terminal_state_identity(obj)
        self.assertEqual(identity, id(obj))

    def test_7_112_two_unsaved_objects_have_distinct_identities(self) -> None:
        """Independent unsaved instances must NEVER collide — each is its own retry stream."""
        a = AtomicCalc(name="a")
        b = AtomicCalc(name="b")
        self.assertNotEqual(
            CalculationModel._terminal_state_identity(a),
            CalculationModel._terminal_state_identity(b),
        )


class TestCluster07e_TerminalStatePersistenceMarker(SimpleTestCase):
    """Scenario 7.113 — the marker that prevents double-persisting an ERROR/SUCCESS row."""

    # -- 7.113 ----------------------------------------------------------
    def test_7_113_mark_records_current_state(self) -> None:
        """``_mark_terminal_state_persisted`` snapshots the current ``is_calculated``."""
        obj = AtomicCalc(name="t7-16")
        obj.is_calculated = CalculationModel.SUCCESS
        CalculationModel._mark_terminal_state_persisted(obj)
        self.assertTrue(
            CalculationModel._has_persisted_terminal_state(
                obj, CalculationModel.SUCCESS,
            )
        )

    def test_7_113_has_persisted_returns_false_when_unset(self) -> None:
        """A fresh object has no persisted-terminal-state marker."""
        obj = AtomicCalc(name="t7-16-fresh")
        self.assertFalse(CalculationModel._has_persisted_terminal_state(obj))

    def test_7_113_clear_removes_marker(self) -> None:
        """``_clear_terminal_state_persistence`` removes the attribute entirely."""
        obj = AtomicCalc(name="t7-16-clear")
        obj.is_calculated = CalculationModel.ERROR
        CalculationModel._mark_terminal_state_persisted(obj)
        CalculationModel._clear_terminal_state_persistence(obj)
        self.assertFalse(CalculationModel._has_persisted_terminal_state(obj))

    def test_7_113_state_specific_check_returns_false_for_other_state(self) -> None:
        """Asking for SUCCESS when ERROR is recorded returns False — markers are state-specific."""
        obj = AtomicCalc(name="t7-16-mixed")
        obj.is_calculated = CalculationModel.ERROR
        CalculationModel._mark_terminal_state_persisted(obj)
        self.assertFalse(
            CalculationModel._has_persisted_terminal_state(
                obj, CalculationModel.SUCCESS,
            )
        )

    def test_7_113_mark_handles_none_object_safely(self) -> None:
        """Defensive: passing ``None`` is a safe no-op (helpers may run after teardown)."""
        # Must not raise.
        CalculationModel._mark_terminal_state_persisted(None)


class TestCluster07e_InProgressStatePersistence(SimpleTestCase):
    """Scenario 7.114–7.115 — IN_PROGRESS marker contracts."""

    # -- 7.114 ----------------------------------------------------------
    def test_7_114_apply_sets_marker_for_in_progress_state(self) -> None:
        """When state is IN_PROGRESS, the marker is set and any pending history is dropped."""
        obj = AtomicCalc(name="t7-17-set")
        # Pre-existing pending history must be cleared once IN_PROGRESS commits.
        setattr(obj, CalculationModel._PENDING_IN_PROGRESS_HISTORY_ATTR, {"x": 1})

        CalculationModel._apply_in_progress_state_persistence(
            obj, CalculationModel.IN_PROGRESS,
        )

        self.assertTrue(CalculationModel._has_persisted_in_progress_state(obj))
        self.assertFalse(
            hasattr(obj, CalculationModel._PENDING_IN_PROGRESS_HISTORY_ATTR),
            "Pending IN_PROGRESS history snapshot must be discarded once "
            "the real IN_PROGRESS row has committed.",
        )

    def test_7_114_apply_clears_marker_for_terminal_state(self) -> None:
        """When state flips to SUCCESS/ERROR, the IN_PROGRESS marker is removed."""
        obj = AtomicCalc(name="t7-17-clear")
        setattr(obj, CalculationModel._IN_PROGRESS_STATE_PERSISTENCE_ATTR, True)

        CalculationModel._apply_in_progress_state_persistence(
            obj, CalculationModel.SUCCESS,
        )

        self.assertFalse(CalculationModel._has_persisted_in_progress_state(obj))

    def test_7_114_apply_handles_none_object_safely(self) -> None:
        """``None`` is a safe no-op."""
        CalculationModel._apply_in_progress_state_persistence(
            None, CalculationModel.IN_PROGRESS,
        )

    # -- 7.115 ----------------------------------------------------------
    def test_7_115_register_immediate_when_not_in_atomic_block(self) -> None:
        """Outside an atomic block the marker is applied immediately — no on_commit."""
        obj = AtomicCalc(name="t7-18-immediate")
        obj.is_calculated = CalculationModel.IN_PROGRESS

        with patch.object(connection, "in_atomic_block", False):
            CalculationModel._register_in_progress_state_persistence(obj)

        self.assertTrue(CalculationModel._has_persisted_in_progress_state(obj))

    def test_7_115_register_defers_to_on_commit_inside_atomic(self) -> None:
        """Inside an atomic block the marker is queued via ``transaction.on_commit``.

        This is the linchpin of the two-phase save: the framework must
        not trust the in-memory marker until the IN_PROGRESS write has
        actually committed, otherwise a rollback would leave a marker
        claiming "IN_PROGRESS persisted" even though the row never made
        it to disk.
        """
        obj = AtomicCalc(name="t7-18-deferred")
        obj.is_calculated = CalculationModel.IN_PROGRESS

        captured_callbacks: list = []

        def fake_on_commit(callback):
            captured_callbacks.append(callback)

        with patch.object(connection, "in_atomic_block", True), \
             patch(
                 "lex.core.models.CalculationModel.transaction.on_commit",
                 side_effect=fake_on_commit,
             ):
            CalculationModel._register_in_progress_state_persistence(obj)

        # Marker has not been applied yet — it's queued on commit.
        self.assertFalse(CalculationModel._has_persisted_in_progress_state(obj))
        self.assertEqual(len(captured_callbacks), 1)

        # Simulate the commit firing.
        captured_callbacks[0]()
        self.assertTrue(CalculationModel._has_persisted_in_progress_state(obj))


class TestCluster07e_MissingInProgressHistoryRecovery(SimpleTestCase):
    """Scenario 7.116 — recovery path that reconstructs IN_PROGRESS history if Phase 1 was rolled back."""

    # -- 7.116 ----------------------------------------------------------
    def test_7_116_queue_skips_when_marker_already_set(self) -> None:
        """If IN_PROGRESS already committed, no recovery snapshot is queued."""
        obj = AtomicCalc(name="t7-19-skip")
        setattr(obj, CalculationModel._IN_PROGRESS_STATE_PERSISTENCE_ATTR, True)
        # Stale pending history must be cleaned up — IN_PROGRESS already exists.
        setattr(obj, CalculationModel._PENDING_IN_PROGRESS_HISTORY_ATTR, {"x": 1})

        CalculationModel._queue_missing_in_progress_history(obj)

        self.assertFalse(
            hasattr(obj, CalculationModel._PENDING_IN_PROGRESS_HISTORY_ATTR),
            "Pending snapshot must be discarded when IN_PROGRESS is "
            "known to be committed.",
        )

    def test_7_116_queue_captures_snapshot_when_marker_missing(self) -> None:
        """Without the marker, a full snapshot + history metadata is stashed for replay."""
        obj = AtomicCalc(name="t7-19-capture")
        obj.is_calculated = CalculationModel.IN_PROGRESS
        obj._history_change_reason = "trigger"

        CalculationModel._queue_missing_in_progress_history(obj)

        pending = getattr(
            obj, CalculationModel._PENDING_IN_PROGRESS_HISTORY_ATTR, None,
        )
        self.assertIsInstance(pending, dict)
        self.assertIn("snapshot", pending)
        self.assertIn("history_date", pending)
        self.assertEqual(pending["history_change_reason"], "trigger")

    def test_7_116_queue_handles_none_safely(self) -> None:
        """``None`` is a safe no-op."""
        CalculationModel._queue_missing_in_progress_history(None)


class TestCluster07e_BeforeSaveIsCreationFlag(SimpleTestCase):
    """Scenario 7.117 — ``before_save`` sets ``is_creation`` based on ``_state.adding``."""

    # -- 7.117 ----------------------------------------------------------
    def test_7_117_new_instance_marked_as_creation(self) -> None:
        """A row being inserted has ``_state.adding == True`` → ``is_creation = True``."""
        obj = AtomicCalc(name="t7-20-new")
        obj._state = SimpleNamespace(adding=True)
        obj.before_save()
        self.assertTrue(
            obj.is_creation,
            "Customer signal handlers branch on ``is_creation`` — must be True for inserts.",
        )

    def test_7_117_existing_instance_not_marked_as_creation(self) -> None:
        """A row being updated has ``_state.adding == False`` → ``is_creation = False``."""
        obj = AtomicCalc(name="t7-20-update")
        obj._state = SimpleNamespace(adding=False)
        obj.before_save()
        self.assertFalse(obj.is_creation)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  Two-phase save — DB-backed, observable contract.
#  These are what a customer would notice: an IN_PROGRESS row visible
#  to other clients while the calculation is running, and a row that
#  ends at ERROR (never stuck at IN_PROGRESS) when the calc crashes.
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━


class TestCluster07e_TwoPhaseCalculationSave(E2ETestCase):
    """Scenarios 7.118–7.121 — Phase 1 commits IN_PROGRESS *before* Phase 2 runs.

    This is the heart of the IN_PROGRESS-survives-failure guarantee.
    Phase 1 is a short atomic block that writes the IN_PROGRESS row;
    Phase 2 runs ``calculate_hook`` outside that block, so even if it
    crashes the IN_PROGRESS row is committed and visible.
    """

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        env_patch = patch.dict(
            os.environ, {"CELERY_ACTIVE": "False"}, clear=False,
        )
        env_patch.start()
        self.addCleanup(env_patch.stop)

    # -- 7.118 ----------------------------------------------------------
    def test_7_118_in_progress_row_visible_to_other_readers_during_calculate(self) -> None:
        """
        Scenario 7.118: While ``calculate()`` is running, a *fresh* DB
        query for the same pk must already see ``is_calculated = IN_PROGRESS``.

        If Phase 1 wrapped Phase 2 in the same atomic block, the
        IN_PROGRESS row would be invisible to other connections until
        the whole save completed — the entire purpose of the spinner
        contract would collapse.
        """
        with OperationContext({}, "phase1-setup"), \
             model_logging_context(None):
            instance = PersistenceProbeCalc.objects.create(name="phase-test")

        observed: dict = {}

        def probe(probe_self):
            # Re-read from the DB *during* calculate(). If Phase 1
            # committed properly we see IN_PROGRESS; if it didn't, we
            # either get NOT_CALCULATED or a DoesNotExist.
            row = PersistenceProbeCalc.objects.get(pk=probe_self.pk)
            observed["mid_calc_status"] = row.is_calculated

        instance._probe = probe
        instance.is_calculated = CalculationModel.IN_PROGRESS

        with OperationContext({}, "phase1"), model_logging_context(instance):
            instance.save()

        self.assertEqual(
            observed.get("mid_calc_status"),
            CalculationModel.IN_PROGRESS,
            "Phase 1 must commit IN_PROGRESS before Phase 2 runs — "
            "otherwise other clients (and the spinner) never see it.",
        )
        instance.refresh_from_db()
        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)

    # -- 7.119 ----------------------------------------------------------
    def test_7_119_terminal_state_marker_recorded_after_success(self) -> None:
        """After a successful save, the SUCCESS terminal-state marker is set on the instance.

        The marker is what protects the framework from re-persisting
        the same terminal state on a Celery callback or retry path.
        """
        with OperationContext({}, "marker-setup"), \
             model_logging_context(None):
            instance = AtomicCalc.objects.create(name="marker-test")

        instance.is_calculated = CalculationModel.IN_PROGRESS
        with OperationContext({}, "marker"), model_logging_context(instance):
            instance.save()

        self.assertEqual(instance.is_calculated, CalculationModel.SUCCESS)
        self.assertTrue(
            CalculationModel._has_persisted_terminal_state(
                instance, CalculationModel.SUCCESS,
            ),
            "After a successful two-phase save the SUCCESS marker must "
            "be recorded — otherwise persist_error_state idempotency "
            "(scenario 7.10) would degrade.",
        )

    # -- 7.120 ----------------------------------------------------------
    def test_7_120_phase_two_failure_leaves_error_row_in_db(self) -> None:
        """If Phase 2 raises, the row ends at ERROR — never stuck at IN_PROGRESS.

        This is the customer-visible promise of the two-phase save: a
        crashed calculation leaves a clearly-failed row, not a
        forever-spinning one.
        """
        with OperationContext({}, "phase2-fail-setup"), \
             model_logging_context(None):
            instance = FailingCalc.objects.create(name="phase2-fail")

        instance.is_calculated = CalculationModel.IN_PROGRESS

        with self.assertRaises(CalculationModelException):
            with OperationContext({}, "phase2-fail"), \
                 model_logging_context(instance):
                instance.save()

        refreshed = FailingCalc.objects.get(pk=instance.pk)
        self.assertEqual(
            refreshed.is_calculated, CalculationModel.ERROR,
            "A stuck IN_PROGRESS would hang the UI spinner — the row "
            "must settle at ERROR.",
        )

    # -- 7.121 ----------------------------------------------------------
    def test_7_121_non_in_progress_save_skips_two_phase(self) -> None:
        """A regular save (no IN_PROGRESS flip) takes the normal single-phase path.

        Two-phase save is reserved for the IN_PROGRESS transition.
        Renaming a row, editing a non-state field, or saving a
        SUCCESS-state row must NOT re-run ``calculate()``.
        """
        with OperationContext({}, "single-phase-setup"), \
             model_logging_context(None):
            instance = AtomicCalc.objects.create(name="single-phase")

        instance.name = "renamed"
        instance.calculate = MagicMock()  # type: ignore[method-assign]
        with OperationContext({}, "single-phase"), \
             model_logging_context(instance):
            instance.save()

        instance.calculate.assert_not_called()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

