"""Startup reset hands recoverable calculations to the recovery supervisor.

Intent
------
On backend boot the startup sweep flips every ``IN_PROGRESS`` calculation row to
``ABORTED``. In a split web/worker deployment the worker pods survive a backend
restart and keep running the work — so a blind sweep aborts live calculations,
and (because PR #603's terminal guard then refuses to requeue a now-terminal
row) permanently loses calculations a dead-but-tracked worker would have been
resumed for. The fix makes the sweep defer to the recovery registry: any row a
tracked recovery task owns (alive heartbeat *or* expired-but-tracked) is left
``IN_PROGRESS`` for the supervisor to finish or resume; only genuinely untracked
rows are aborted. When recovery is off / Redis is down the registry reports
nothing tracked, so behavior is identical to today.

Cluster 8x — scenarios 8.103–8.114. Type: U (helper logic, mocked registry) + I
(real ``CalculationModel`` rows driven through the startup sweep).
Covers: lex/lex_app/celery_recovery/supervisor.py,
        lex/process_admin/utils/model_registration.py.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8x_startup_reset_recovery_handoff.py -v
"""

from __future__ import annotations

import os
from unittest import mock

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.celery_recovery import supervisor
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08x_TrackedRecordIds(SimpleTestCase):
    """Cluster 8x: the registry → owned-record-ids lookup the sweep relies on."""

    def _instance(self, pk):
        """An unsaved CalculationModel instance carrying a pk (no DB needed)."""
        inst = CelerySyncCalc(name=f"calc-{pk}")
        inst.pk = pk
        return inst

    def _patch_registry(self, *, tracked, payloads):
        """Patch list_tracked + get_payload for the given task→payload map."""
        return [
            mock.patch.object(supervisor.registry, "list_tracked", return_value=tracked),
            mock.patch.object(
                supervisor.registry, "get_payload",
                side_effect=lambda tid: payloads.get(tid),
            ),
        ]

    def test_08_103_alive_tracked_task_contributes_its_rows(self):
        """
        Scenario 8.103: a tracked task's calc rows are reported as owned.
        Given: one tracked task whose payload args carry a calc row (pk=7).
        When:  tracked_calculation_record_ids() is computed.
        Then:  the (label_lower, 7) pair is in the returned set — the sweep will
               skip that row instead of aborting it.
        """
        inst = self._instance(7)
        payloads = {"t1": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertIn((CelerySyncCalc._meta.label_lower, 7), result)

    def test_08_104_expired_but_tracked_task_still_contributes(self):
        """
        Scenario 8.104: ownership does NOT depend on a live heartbeat.
        Given: a tracked task (its worker died — is_alive would be False) whose
               payload still carries a calc row.
        When:  tracked_calculation_record_ids() is computed.
        Then:  the row is still reported owned — the supervisor will requeue and
               resume it, so the startup sweep must not abort it. (No is_alive
               call is made; ownership = tracked-at-all.)
        """
        inst = self._instance(9)
        payloads = {"dead": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["dead"], payloads=payloads)
        with p1, p2, mock.patch.object(supervisor.registry, "is_alive") as alive:
            result = supervisor.tracked_calculation_record_ids()
        self.assertIn((CelerySyncCalc._meta.label_lower, 9), result)
        alive.assert_not_called()

    def test_08_105_empty_registry_returns_empty_set(self):
        """
        Scenario 8.105: recovery off / Redis down → nothing owned → back-compat.
        Given: registry.list_tracked() returns [] (disabled or unreadable).
        When:  tracked_calculation_record_ids() is computed.
        Then:  it returns an empty set, so the sweep aborts every stuck row
               exactly as it does today.
        """
        p1, p2 = self._patch_registry(tracked=[], payloads={})
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())

    def test_08_106_payload_without_calc_instances_contributes_nothing(self):
        """
        Scenario 8.106: a tracked non-calc task owns no rows.
        Given: a tracked task whose payload args carry no CalculationModel.
        When:  tracked_calculation_record_ids() is computed.
        Then:  it contributes nothing — unrelated stuck rows still abort.
        """
        payloads = {"t1": {"args": (["not-a-model"],), "name": "load_data"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())

    def test_08_107_multiple_tasks_are_unioned(self):
        """
        Scenario 8.107: every tracked task's rows are collected.
        Given: two tracked tasks, each owning a distinct calc row.
        When:  tracked_calculation_record_ids() is computed.
        Then:  both (label, pk) pairs are present — ownership is the union.
        """
        a, b = self._instance(1), self._instance(2)
        payloads = {
            "t1": {"args": ([a],), "name": "calc_and_save"},
            "t2": {"args": ([b],), "name": "calc_and_save"},
        }
        p1, p2 = self._patch_registry(tracked=["t1", "t2"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(
            result,
            {(CelerySyncCalc._meta.label_lower, 1),
             (CelerySyncCalc._meta.label_lower, 2)},
        )

    def test_08_108_instance_without_pk_is_excluded(self):
        """
        Scenario 8.108: a row with no pk can never match a stuck DB row.
        Given: a tracked task whose calc instance has pk=None.
        When:  tracked_calculation_record_ids() is computed.
        Then:  it is excluded — only persisted rows (with a pk) are protectable.
        """
        inst = CelerySyncCalc(name="no-pk")  # pk stays None
        payloads = {"t1": {"args": ([inst],), "name": "calc_and_save"}}
        p1, p2 = self._patch_registry(tracked=["t1"], payloads=payloads)
        with p1, p2:
            result = supervisor.tracked_calculation_record_ids()
        self.assertEqual(result, set())


class TestCluster08x_StartupSweepDefersToOwnership(E2ETestCase):
    """Cluster 8x: the startup sweep skips rows the recovery registry owns.

    Real ``CalculationModel`` rows are driven straight through
    ``_handle_calculation_model_reset`` with the recovery-ownership set injected,
    so the abort/skip decision is the genuine one. The default E2E patch already
    mocks ``ensure_terminal_calculation_audit``; we read that mock from
    ``self._patch_map`` to prove whether an audit row would have been written.
    """

    e2e_models = [CelerySyncCalc]

    def _make_in_progress(self, name):
        row = CelerySyncCalc.objects.create(name=name)
        row.is_calculated = CalculationModel.IN_PROGRESS
        row.save(skip_hooks=True)
        return row

    def _owned(self, *rows):
        return {(CelerySyncCalc._meta.label_lower, r.pk) for r in rows}

    def _run_sweep(self, tracked_record_ids):
        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                CelerySyncCalc, tracked_record_ids=tracked_record_ids,
            )

    def test_08_109_owned_row_stays_in_progress_and_is_not_audited(self):
        """
        Scenario 8.109: a row a tracked task owns is left for recovery.
        Given: an IN_PROGRESS row whose (label, pk) is in the owned set.
        When:  the startup sweep runs.
        Then:  the row stays IN_PROGRESS and no aborted-audit is written — the
               worker (alive) or the supervisor (resume) will conclude it.
        """
        row = self._make_in_progress("live")
        audit = self._patch_map["ensure_terminal_calculation_audit"]
        self._run_sweep(self._owned(row))
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)
        audit.assert_not_called()

    def test_08_110_untracked_row_is_aborted_and_audited(self):
        """
        Scenario 8.110: an unowned row is the only thing the sweep aborts.
        Given: an IN_PROGRESS row that no tracked task owns (empty owned set).
        When:  the startup sweep runs.
        Then:  the row flips to ABORTED and an aborted-audit is written — today's
               behavior, preserved for genuinely unrecoverable rows.
        """
        row = self._make_in_progress("orphan")
        audit = self._patch_map["ensure_terminal_calculation_audit"]
        self._run_sweep(set())
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.ABORTED)
        audit.assert_called_once()

    def test_08_111_mixed_rows_only_unowned_is_aborted(self):
        """
        Scenario 8.111: ownership is per-row, not all-or-nothing.
        Given: two IN_PROGRESS rows — one owned by a tracked task, one not.
        When:  the startup sweep runs.
        Then:  the owned row stays IN_PROGRESS, the unowned row goes ABORTED.
        """
        owned_row = self._make_in_progress("keep")
        orphan_row = self._make_in_progress("drop")
        self._run_sweep(self._owned(owned_row))
        owned_row.refresh_from_db()
        orphan_row.refresh_from_db()
        self.assertEqual(owned_row.is_calculated, CalculationModel.IN_PROGRESS)
        self.assertEqual(orphan_row.is_calculated, CalculationModel.ABORTED)

    def test_08_112_empty_ownership_aborts_all_rows_backcompat(self):
        """
        Scenario 8.112: recovery off → identical to the original blind sweep.
        Given: two IN_PROGRESS rows and an empty owned set (recovery disabled /
               Redis down — tracked_calculation_record_ids() returns set()).
        When:  the startup sweep runs.
        Then:  both rows are aborted — no regression when recovery is unavailable.
        """
        r1 = self._make_in_progress("a")
        r2 = self._make_in_progress("b")
        self._run_sweep(set())
        r1.refresh_from_db()
        r2.refresh_from_db()
        self.assertEqual(r1.is_calculated, CalculationModel.ABORTED)
        self.assertEqual(r2.is_calculated, CalculationModel.ABORTED)

    def test_08_113_gate_off_is_a_noop(self):
        """
        Scenario 8.113: without CALLED_FROM_START_COMMAND the sweep does nothing.
        Given: an IN_PROGRESS row and the start-command gate unset.
        When:  _handle_calculation_model_reset is invoked.
        Then:  the row is untouched — the gate still fully short-circuits the
               sweep, so the new ownership logic never runs outside startup.
        """
        row = self._make_in_progress("gated")
        with mock.patch.dict(os.environ, {}, clear=False):
            # Relies on the gate being the sole short-circuit, so the ownership
            # branch never runs outside startup once the env var is removed.
            os.environ.pop("CALLED_FROM_START_COMMAND", None)
            ModelRegistration._handle_calculation_model_reset(
                CelerySyncCalc, tracked_record_ids=set(),
            )
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)

    def test_08_114_precomputed_set_skips_the_registry_read(self):
        """
        Scenario 8.114: passing the set in avoids a per-model registry hit.
        Given: a precomputed tracked_record_ids is supplied to the sweep.
        When:  the sweep runs.
        Then:  it uses the given set and does NOT call
               tracked_calculation_record_ids() again — the caller computes once
               and threads it through the per-model loop.
        """
        row = self._make_in_progress("precomputed")
        with mock.patch.object(
            supervisor, "tracked_calculation_record_ids",
        ) as compute:
            self._run_sweep(self._owned(row))
        compute.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)

    def test_08_115_no_kwarg_self_computes_ownership_from_registry(self):
        """
        Scenario 8.115: a direct caller omitting the kwarg self-computes.
        Given: an IN_PROGRESS row the registry reports as owned, and the sweep is
               invoked with no tracked_record_ids argument (back-compat path used
               by existing direct callers).
        When:  _handle_calculation_model_reset runs.
        Then:  it calls tracked_calculation_record_ids() itself and honors the
               result — the owned row stays IN_PROGRESS. This exercises the
               lazy-import self-compute branch the caller normally precomputes.
        """
        row = self._make_in_progress("self-compute")
        with mock.patch.object(
            supervisor, "tracked_calculation_record_ids",
            return_value=self._owned(row),
        ) as compute, mock.patch.dict(
            os.environ, {"CALLED_FROM_START_COMMAND": "1"}
        ):
            ModelRegistration._handle_calculation_model_reset(CelerySyncCalc)
        compute.assert_called_once()
        row.refresh_from_db()
        self.assertEqual(row.is_calculated, CalculationModel.IN_PROGRESS)
