"""Cluster 7s — a calculation must never move edited_at / edited_by.

Intent: ``edited_at`` and ``edited_by`` answer "who last *edited* this record,
and when". A calculation is system-triggered work, not a user edit, so running
one — however it ends (SUCCESS, ERROR, CANCELLED, or ABORTED by a startup
recovery sweep) — must leave both columns untouched. Only a real change to
project-defined fields may stamp them. Both are audit-relevant columns, so a
regression here silently corrupts the compliance trail: it makes an automated
recalculation indistinguishable from a human edit.

The framework's mechanism is ``LexModel._should_skip_edited_fields_update()``.
Suppression must hold by construction, not via a completion-time revert: the
calculation *trigger* save itself would otherwise stamp the fields, and an
interrupted calculation (server restart mid-run) never reaches the revert —
leaving a stale stamp on a record marked ABORTED. This batch pins the contract
on the Celery-OFF paths, driven through the **real HTTP ``calculate=true``
endpoint** (7.218–7.221) as the UI does, not only the programmatic trigger.
The Celery dispatch paths are cluster 8c.

The negative controls (7.213–7.215) are as important as the positives: a fix
that over-suppresses would stop stamping ``edited_at`` on genuine user edits,
breaking the audit trail in the opposite direction.

Cluster 7s — scenarios 7.205–7.221. Type: I/E.
Covers: lex/core/models/LexModel.py (_should_skip_edited_fields_update,
        update_edited_at, update_edited_by),
        lex/core/models/CalculationModel.py (execute_calculation_sync),
        lex/process_admin/utils/model_registration.py
        (_handle_calculation_model_reset).
Run: python -m lex pytest lex/test_project/tests/calculations/test_7s_calculation_audit_columns.py -v
"""

from __future__ import annotations

import os
import time
from unittest import mock

import pytest
from django.utils import timezone

from lex.core.models.CalculationModel import CalculationModel
from lex.process_admin.utils.model_registration import ModelRegistration
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AUDIT_CALC,
    AuditColumnsCalc,
    AuditColumnsCancelCalc,
    AuditColumnsChild,
    AuditColumnsFailingCalc,
    AuditColumnsParentCalc,
)

pytestmark = pytest.mark.calculations


class TestCluster07s_CalculationAuditColumns(E2ETestCase):
    """Cluster 7s: sync calculations and the startup sweep leave audit columns alone."""

    e2e_models = ALL_MODELS

    # -- helpers -------------------------------------------------------
    def _seed(self, model_cls, name, **kwargs):
        """Create a row and give it a real user edit so edited_at is populated."""
        item = model_cls.objects.create(name=name, **kwargs)
        item.name = f"{name}-edited"
        item.save()
        fresh = model_cls.objects.get(pk=item.pk)
        assert fresh.edited_at is not None, "fixture must start with edited_at set"
        return fresh.pk, fresh.edited_at, fresh.edited_by

    def _run_calc(self, model_cls, pk):
        """Trigger the calculation the way project code does, then settle."""
        obj = model_cls.objects.get(pk=pk)
        obj.is_calculated = CalculationModel.IN_PROGRESS
        try:
            obj.save()
        except Exception:
            # ERROR / CANCELLED paths surface the exception to the caller;
            # the terminal state is still persisted by the framework.
            pass
        time.sleep(0.5)
        return model_cls.objects.get(pk=pk)

    # -- 7.205 / 7.206 — sync SUCCESS ----------------------------------
    def test_7_205_sync_success_does_not_move_edited_at(self) -> None:
        """
        Scenario 7.205: a successful sync calculation leaves edited_at alone.
        Given: a record with edited_at set by a real user edit
        When: its calculation runs to SUCCESS (calculate() saves the record)
        Then: edited_at is byte-identical to before the calculation
        """
        pk, before_at, _ = self._seed(AuditColumnsCalc, "s205")
        after = self._run_calc(AuditColumnsCalc, pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.SUCCESS,
            f"precondition: calculation should have succeeded, got {after.is_calculated}.",
        )
        self.assertEqual(
            after.edited_at, before_at,
            f"a calculation is not a user edit; edited_at moved "
            f"{before_at} -> {after.edited_at}.",
        )

    def test_7_206_sync_success_does_not_move_edited_by(self) -> None:
        """
        Scenario 7.206: the same holds for edited_by.
        Given: a record whose edited_by was set by a real user edit
        When: its calculation runs to SUCCESS
        Then: edited_by is unchanged — the calc must not claim authorship
        """
        pk, _, before_by = self._seed(AuditColumnsCalc, "s206")
        after = self._run_calc(AuditColumnsCalc, pk)
        self.assertEqual(
            after.edited_by, before_by,
            f"edited_by must not be reattributed by a calculation; "
            f"{before_by!r} -> {after.edited_by!r}.",
        )

    # -- 7.207 — ERROR --------------------------------------------------
    def test_7_207_sync_error_does_not_move_audit_columns(self) -> None:
        """
        Scenario 7.207: a failing calculation leaves both columns alone.
        Given: a record whose calculate() saves then raises
        When: the calculation ends in ERROR
        Then: neither edited_at nor edited_by moved
        """
        pk, before_at, before_by = self._seed(AuditColumnsFailingCalc, "s207")
        after = self._run_calc(AuditColumnsFailingCalc, pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.ERROR,
            f"precondition: expected ERROR terminal state, got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            f"a failed calculation must not stamp audit columns "
            f"(edited_at {before_at} -> {after.edited_at}).",
        )

    # -- 7.208 — CANCELLED ---------------------------------------------
    def test_7_208_sync_cancellation_does_not_move_audit_columns(self) -> None:
        """
        Scenario 7.208: a cooperatively cancelled calculation leaves them alone.
        Given: a record whose calculate() raises CalculationCancelled
        When: the calculation settles as CANCELLED
        Then: neither edited_at nor edited_by moved
        """
        pk, before_at, before_by = self._seed(AuditColumnsCancelCalc, "s208")
        after = self._run_calc(AuditColumnsCancelCalc, pk)
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            f"a cancelled calculation must not stamp audit columns "
            f"(state={after.is_calculated}, edited_at {before_at} -> {after.edited_at}).",
        )

    # -- 7.209 — child records ------------------------------------------
    def test_7_209_child_saved_during_parent_calc_keeps_audit_columns(self) -> None:
        """
        Scenario 7.209: rows a calculation updates as *output* are system writes.
        Given: a pre-existing child row with edited_at set by a user edit
        When: a parent calculation updates and saves that child
        Then: the child's edited_at/edited_by are unchanged — the parent's
              calculation is not a human editing the child
        """
        child_pk, child_at, child_by = self._seed(AuditColumnsChild, "s209-child")
        pk, _, _ = self._seed(AuditColumnsParentCalc, "s209", child_pk=child_pk)

        self._run_calc(AuditColumnsParentCalc, pk)

        child = AuditColumnsChild.objects.get(pk=child_pk)
        self.assertEqual(
            child.payload, 1,
            "precondition: the parent calculation should have written the child.",
        )
        self.assertEqual(
            (child.edited_at, child.edited_by), (child_at, child_by),
            f"child rows written by a calculation must keep their audit columns; "
            f"edited_at {child_at} -> {child.edited_at}.",
        )

    # -- 7.210 — created_at ---------------------------------------------
    def test_7_210_calculation_never_moves_created_at(self) -> None:
        """
        Scenario 7.210: created_at is immutable across a calculation.
        Given: a record with a known created_at
        When: its calculation runs
        Then: created_at is unchanged (it records creation, not recalculation)
        """
        pk, _, _ = self._seed(AuditColumnsCalc, "s210")
        before_created = AuditColumnsCalc.objects.get(pk=pk).created_at
        after = self._run_calc(AuditColumnsCalc, pk)
        self.assertEqual(
            after.created_at, before_created,
            f"created_at must never move; {before_created} -> {after.created_at}.",
        )

    # -- 7.211 — startup sweep ------------------------------------------
    def test_7_211_startup_sweep_abort_does_not_move_audit_columns(self) -> None:
        """
        Scenario 7.211: a server restart that aborts a stuck calc is not an edit.
        Given: a record left IN_PROGRESS (the server died mid-calculation)
        When: the startup recovery sweep flips it to ABORTED
        Then: edited_at/edited_by are unchanged — nobody edited the record,
              the framework gave up on its behalf
        """
        pk, before_at, before_by = self._seed(AuditColumnsCalc, "s211")
        AuditColumnsCalc.objects.filter(pk=pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=set()
            )

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.ABORTED,
            f"precondition: the sweep should have aborted the stuck row, "
            f"got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            f"the startup abort must not stamp audit columns; "
            f"edited_at {before_at} -> {after.edited_at}.",
        )

    # -- 7.212 — recovery-tracked row -----------------------------------
    def test_7_212_sweep_skips_tracked_row_without_touching_audit_columns(self) -> None:
        """
        Scenario 7.212: a row owned by the recovery machinery is left alone.
        Given: a stuck IN_PROGRESS row that IS tracked (a worker will finish it)
        When: the startup sweep runs
        Then: it stays IN_PROGRESS and its audit columns are untouched
        """
        pk, before_at, before_by = self._seed(AuditColumnsCalc, "s212")
        AuditColumnsCalc.objects.filter(pk=pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )
        tracked = {(AuditColumnsCalc._meta.label_lower, pk)}

        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=tracked
            )

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.IN_PROGRESS,
            "a recovery-tracked row must be left IN_PROGRESS for the worker.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, before_by),
            "skipping a tracked row must not stamp audit columns.",
        )

    # -- 7.213 — NEGATIVE CONTROL: real user edit -----------------------
    def test_7_213_real_user_edit_still_stamps_edited_at(self) -> None:
        """
        Scenario 7.213: suppression must not leak into genuine user edits.
        Given: an existing record
        When: a user changes a project-defined field through the REST API
        Then: edited_at DOES move — this is the behaviour the audit trail needs,
              and the guard against a fix that over-suppresses
        """
        pk, before_at, _ = self._seed(AuditColumnsCalc, "s213")
        resp = self.client.patch(
            self.url_detail(AUDIT_CALC, pk),
            data={"name": "s213-user-edit"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 202), f"update failed: {resp.status_code}")

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertNotEqual(
            after.edited_at, before_at,
            "a genuine user edit MUST stamp edited_at — suppression has leaked "
            "into the normal edit path and the audit trail is now blind.",
        )

    # -- 7.214 — NEGATIVE CONTROL: suppression must not outlive the calc -
    def test_7_214_user_edit_after_calculation_still_stamps(self) -> None:
        """
        Scenario 7.214: the suppression window closes when the calculation ends.
        Given: a record whose calculation has just run to completion
        When: a user then edits a project-defined field
        Then: edited_at moves — suppression is scoped to the calculation and
              must not leak past it via a sticky flag or an unreset ContextVar
              (the specific failure mode a context-manager fix can introduce)
        """
        pk, _, _ = self._seed(AuditColumnsCalc, "s214")
        self._run_calc(AuditColumnsCalc, pk)
        after_calc = AuditColumnsCalc.objects.get(pk=pk).edited_at

        resp = self.client.patch(
            self.url_detail(AUDIT_CALC, pk),
            data={"name": "s214-user-edit-after-calc"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 202), f"update failed: {resp.status_code}")

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.name, "s214-user-edit-after-calc",
            "precondition: the user's edit should have been applied.",
        )
        self.assertNotEqual(
            after.edited_at, after_calc,
            "a user edit AFTER a calculation must stamp edited_at — the "
            "calculation's suppression has leaked beyond its window.",
        )

    # -- 7.215 — explicit override --------------------------------------
    def test_7_215_explicit_edited_at_override_is_honoured(self) -> None:
        """
        Scenario 7.215: project code may set edited_at deliberately.
        Given: code assigns an explicit edited_at and saves
        When: the save runs
        Then: the explicit value survives — the framework does not overwrite
              a value the project set on purpose (e.g. back-dated imports)
        """
        pk, _, _ = self._seed(AuditColumnsCalc, "s215")
        explicit = timezone.now() - timezone.timedelta(days=30)

        obj = AuditColumnsCalc.objects.get(pk=pk)
        obj.name = "s215-explicit"
        obj.edited_at = explicit
        obj.save()

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.edited_at, explicit,
            f"an explicitly assigned edited_at must be honoured, not overwritten "
            f"with now(); got {after.edited_at}.",
        )

    # -- 7.217 — startup sweep must not reattribute authorship ----------
    def test_7_217_startup_sweep_does_not_reattribute_edited_by(self) -> None:
        """
        Scenario 7.217: restarting the server must not rewrite *who* last
        edited a record that was mid-calculation.
        Given: Celery is OFF, a record is already IN_PROGRESS in the database
               (the server died mid-calculation), and its edited_by names a
               real person
        When: the server starts and the recovery sweep aborts the stuck row
        Then: edited_by still names that person — a server restart is not an
              edit by anyone, least of all by the framework's fallback actor

        Uses a distinct sentinel rather than the fixture's default actor: the
        sweep resolves the same fallback actor a normal save would, so an
        identical overwrite would be undetectable and the assertion would pass
        for the wrong reason (cf. 8.147).
        """
        pk, before_at, _ = self._seed(AuditColumnsCalc, "s217")
        sentinel = "a.human@example.com"
        # bypass hooks so the sentinel survives until the sweep runs
        AuditColumnsCalc.objects.filter(pk=pk).update(
            edited_by=sentinel,
            is_calculated=CalculationModel.IN_PROGRESS,
        )

        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=set()
            )

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.ABORTED,
            f"precondition: the sweep should have aborted the stuck row, "
            f"got {after.is_calculated}.",
        )
        self.assertEqual(
            after.edited_by, sentinel,
            f"the startup abort reattributed authorship: edited_by "
            f"{sentinel!r} -> {after.edited_by!r}. A restart is not an edit, "
            f"and the audit trail must keep naming the real editor.",
        )
        self.assertEqual(
            after.edited_at, before_at,
            f"the startup abort also moved edited_at ({before_at} -> "
            f"{after.edited_at}).",
        )

    # -- 7.216 — fan-out output rows ------------------------------------
    def test_7_216_fanout_calculation_keeps_related_row_audit_columns(self) -> None:
        """
        Scenario 7.216: repeated recalculation never accumulates edit stamps.
        Given: a child row with edited_at set, updated by a parent calculation
        When: the parent calculation is run twice in a row
        Then: the child was written both times, yet its audit columns never moved
        """
        child_pk, child_at, child_by = self._seed(AuditColumnsChild, "s216-child")
        pk, _, _ = self._seed(AuditColumnsParentCalc, "s216", child_pk=child_pk)

        self._run_calc(AuditColumnsParentCalc, pk)
        self._run_calc(AuditColumnsParentCalc, pk)

        child = AuditColumnsChild.objects.get(pk=child_pk)
        self.assertEqual(
            child.payload, 2,
            "precondition: both calculation runs should have written the child.",
        )
        self.assertEqual(
            (child.edited_at, child.edited_by), (child_at, child_by),
            f"repeated recalculation must not accumulate edit stamps; "
            f"edited_at {child_at} -> {child.edited_at}.",
        )

    # -- 7.218–7.221 — the REAL HTTP calculate=true trigger -------------
    # The UI triggers a calculation with PATCH ?calculate=true, which routes
    # through One.update -> perform_update. That save path is distinct from the
    # programmatic trigger above and is where the reported stamp originated:
    # the trigger save carries _defer_calculate_hook and must be suppressed by
    # the guard, so the fields survive even when the calculation is interrupted
    # before the completion-time revert can run.

    SENTINEL = "a.human@example.com"

    def _seed_http(self, name):
        pk, before_at, _ = self._seed(AuditColumnsCalc, name)
        # a real, non-fallback editor so an identical-value overwrite can't hide
        AuditColumnsCalc.objects.filter(pk=pk).update(edited_by=self.SENTINEL)
        return pk, AuditColumnsCalc.objects.get(pk=pk).edited_at

    def test_7_218_http_calculate_success_keeps_audit_columns(self) -> None:
        """
        Scenario 7.218: a UI-triggered calculation that completes leaves the
        columns alone — and not merely because completion reverts them.
        Given: a record whose edited_by names a real person
        When: PATCH ?calculate=true runs to SUCCESS
        Then: edited_at unchanged and edited_by still names that person
        """
        pk, before_at = self._seed_http("s218")
        resp = self.client.patch(
            self.url_detail(AUDIT_CALC, pk),
            data={"calculate": "true"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 202), f"trigger failed: {resp.status_code}")
        time.sleep(1.0)

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.SUCCESS,
            f"precondition: expected SUCCESS, got {after.is_calculated}.",
        )
        self.assertEqual(
            (after.edited_at, after.edited_by), (before_at, self.SENTINEL),
            f"a UI-triggered calculation stamped audit columns; edited_at "
            f"{before_at} -> {after.edited_at}, edited_by -> {after.edited_by!r}.",
        )

    def test_7_219_http_calculate_interrupted_then_restart(self) -> None:
        """
        Scenario 7.219: THE reported case — a UI-triggered calculation that is
        interrupted by a server restart must not leave a stale stamp.
        Given: a record whose edited_by names a real person, Celery OFF
        When: PATCH ?calculate=true is issued but the server restarts before the
              calculation completes (calculate_hook never runs), then the
              startup sweep aborts the stuck row
        Then: the record is ABORTED, yet edited_at and edited_by are unchanged —
              the trigger save must not stamp, because no completion-time revert
              runs to undo it
        """
        pk, before_at = self._seed_http("s219")

        # server dies after the trigger but before the calculation runs
        with mock.patch.object(CalculationModel, "calculate_hook",
                               lambda self, *a, **k: None):
            resp = self.client.patch(
                self.url_detail(AUDIT_CALC, pk),
                data={"calculate": "true"},
                format="json",
            )
            self.assertIn(resp.status_code, (200, 202), f"trigger failed: {resp.status_code}")
            time.sleep(0.3)

        # the row is genuinely stuck IN_PROGRESS
        AuditColumnsCalc.objects.filter(pk=pk).update(
            is_calculated=CalculationModel.IN_PROGRESS
        )

        # restart: the recovery sweep aborts it
        with mock.patch.dict(os.environ, {"CALLED_FROM_START_COMMAND": "1"}):
            ModelRegistration._handle_calculation_model_reset(
                AuditColumnsCalc, tracked_record_ids=set()
            )

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            after.is_calculated, CalculationModel.ABORTED,
            f"precondition: the interrupted row should have been aborted, "
            f"got {after.is_calculated}.",
        )
        self.assertEqual(
            after.edited_at, before_at,
            f"an interrupted UI calculation left a stale edited_at "
            f"({before_at} -> {after.edited_at}) on an ABORTED record.",
        )
        self.assertEqual(
            after.edited_by, self.SENTINEL,
            f"an interrupted UI calculation reattributed edited_by "
            f"({self.SENTINEL!r} -> {after.edited_by!r}).",
        )

    def test_7_220_http_calculate_interrupted_stamp_absent_before_restart(self) -> None:
        """
        Scenario 7.220: the stamp must be absent the moment the trigger returns,
        not merely undone later.
        Given: a record whose edited_by names a real person
        When: PATCH ?calculate=true is issued and the row is inspected while
              still IN_PROGRESS (calculation not yet complete)
        Then: edited_at/edited_by are already unchanged — the trigger save
              itself did not stamp (proves the guard, not a revert)
        """
        pk, before_at = self._seed_http("s220")
        with mock.patch.object(CalculationModel, "calculate_hook",
                               lambda self, *a, **k: None):
            self.client.patch(
                self.url_detail(AUDIT_CALC, pk),
                data={"calculate": "true"},
                format="json",
            )
            time.sleep(0.3)

        mid = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(
            mid.is_calculated, CalculationModel.IN_PROGRESS,
            f"precondition: the row should be mid-calculation, got {mid.is_calculated}.",
        )
        self.assertEqual(
            (mid.edited_at, mid.edited_by), (before_at, self.SENTINEL),
            f"the trigger save stamped audit columns before any completion "
            f"could revert them; edited_at {before_at} -> {mid.edited_at}.",
        )

    def test_7_221_http_real_edit_still_stamps(self) -> None:
        """
        Scenario 7.221: negative control on the HTTP path — a plain field edit
        (no calculate) must still stamp.
        Given: an existing record
        When: PATCH changes a project-defined field, without calculate=true
        Then: edited_at moves — the guard must not suppress genuine edits made
              through the same endpoint
        """
        pk, before_at = self._seed_http("s221")
        resp = self.client.patch(
            self.url_detail(AUDIT_CALC, pk),
            data={"name": "s221-user-edit"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 202), f"update failed: {resp.status_code}")

        after = AuditColumnsCalc.objects.get(pk=pk)
        self.assertEqual(after.name, "s221-user-edit", "precondition: edit should apply.")
        self.assertNotEqual(
            after.edited_at, before_at,
            "a genuine field edit through the HTTP endpoint must still stamp "
            "edited_at — the calculation guard has leaked into normal edits.",
        )
