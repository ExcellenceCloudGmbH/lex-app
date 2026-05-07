"""
Cluster 6b: Calculation audit finalization.

Intent (from docs/features/audit-logging/ and
docs/features/calculations/):

    Every root calculation ends with a *terminal* AuditLog row whose
    ``AuditLogStatus`` is ``'success'`` or ``'failure'``. On failure
    the status carries the traceback so operators can diagnose it.

    Crucially, the terminal audit must survive a calculation rollback
    (scenario 6.10 — BUG-001 family): even when the calculation's
    atomic transaction rolls back, the audit of *why* it failed must
    remain.

The Pass B2 fixture unblocks these scenarios by:

    * creating the ``AuditLog`` / ``AuditLogStatus`` tables in
      ``e2e_framework_models``;
    * unpatching ``ensure_terminal_calculation_audit`` so the real
      function runs against the live tables.

Scenario numbering matches
docs/test-plan/test-clusters.md#6-audit-logging.
"""

from __future__ import annotations

import unittest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.utils.calculation_audit import (
    ensure_terminal_calculation_audit,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, AuditAtomicCalc

AUDIT_ATOMIC = "auditatomiccalc"


class TestCluster06b_CalculationAudit(E2ETestCase):
    """Calculation audit finalization — live under the Pass B2 fixture."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real function run — it writes to the live tables above.
    e2e_unpatch = {"ensure_terminal_calculation_audit"}

    def test_6_5_calc_success_terminal_audit(self) -> None:
        """
        Scenario 6.5: Successful calculation → terminal AuditLog with
        ``AuditLogStatus.status = 'success'``.

        We call the terminal-audit function directly with a saved root
        calculation instance; it mirrors exactly what the calc state
        machine does on SUCCESS.
        """
        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-5", should_fail=False)

        result = ensure_terminal_calculation_audit(
            calc,
            audit_status="success",
            context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        self.assertIsNotNone(
            result,
            "ensure_terminal_calculation_audit must return the AuditLog "
            "row it created or updated for a root calculation.",
        )
        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        self.assertEqual(
            rows.count(), 1,
            "A successful root calculation must produce exactly one "
            f"terminal AuditLog row; got {rows.count()}.",
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(audit_log=rows.first()).first().status,
            "success",
            "A successful calculation must finalize AuditLogStatus to "
            "'success'.",
        )

    def test_6_6_calc_failure_terminal_audit(self) -> None:
        """
        Scenario 6.6: Failed calculation → terminal AuditLog with
        ``status='failure'`` and a traceback attached.

        The traceback is what lets an operator diagnose the failure
        after the fact — without it the audit trail is useless.
        """
        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-6")

        result = ensure_terminal_calculation_audit(
            calc,
            audit_status="failure",
            error_message="boom",
            stack_trace="Traceback (simulated):\n  RuntimeError: boom",
            context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        self.assertIsNotNone(result)
        status_row = AuditLogStatus.objects.filter(audit_log=result).first()
        self.assertEqual(
            status_row.status, "failure",
            "A failed calculation must finalize AuditLogStatus to 'failure'.",
        )
        self.assertIn(
            "boom", (status_row.error_traceback or ""),
            "The terminal-audit failure row must carry the traceback "
            "so operators can diagnose the failure after the fact; "
            f"got {status_row.error_traceback!r}.",
        )

    @unittest.expectedFailure  # BUG-001 family: outer atomic.rollback wipes inner atomic block; needs separate-connection write
    def test_6_10_audit_survives_calculation_failure(self) -> None:
        """
        Scenario 6.10: Audit must survive a calculation's atomic rollback.

        Documented intent: ``ensure_terminal_calculation_audit`` uses a
        **separate** transaction so the audit row persists even when the
        outer calculation transaction rolls back.

        Expected failure: when the outer transaction is still active and
        rolls back, the inner audit row is rolled back too — the
        "separate transaction" guarantee hasn't been implemented yet.
        Linked to BUG-001 family (save() + IN_PROGRESS + hooks all in
        one atomic block).
        """
        from django.db import transaction

        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10")

        try:
            with transaction.atomic():
                ensure_terminal_calculation_audit(
                    calc,
                    audit_status="failure",
                    error_message="rollback test",
                    stack_trace="Traceback (rollback):",
                    context_data={"calculation_id": f"calc_{calc.pk}"},
                )
                # Force the outer transaction to abort.
                raise RuntimeError("outer rollback")
        except RuntimeError:
            pass

        self.assertEqual(
            AuditLog.objects.filter(
                resource="auditatomiccalc", object_id=calc.pk,
            ).count(),
            1,
            "Terminal audit row must survive an outer transaction "
            "rollback — the documented contract is that failure "
            "diagnostics are never lost, no matter what happens to the "
            "calculation itself.",
        )

    # -- 6.10-control: sanity check the test infrastructure ------------
    def test_6_10_control_audit_persists_without_outer_atomic(self) -> None:
        """
        Control / sanity test for the BUG-001b family.

        Identical assertions to 6.10 but with NO enclosing
        ``transaction.atomic()``. Should pass cleanly. If this ever
        starts failing, the issue is in the test infrastructure, not
        the bug — i.e. ``ensure_terminal_calculation_audit`` is broken
        for the trivial path. This pins down where the rollback
        damage actually originates.
        """
        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10-control")

        ensure_terminal_calculation_audit(
            calc,
            audit_status="failure",
            error_message="happy-path",
            stack_trace="Traceback (no outer atomic):",
            context_data={"calculation_id": f"calc_{calc.pk}"},
        )

        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        self.assertEqual(
            rows.count(), 1,
            "With no enclosing atomic, the terminal audit must land. "
            "If this fails, the bug is in the audit writer itself, "
            "not in the rollback interaction.",
        )
        self.assertEqual(
            AuditLogStatus.objects.filter(
                audit_log=rows.first()
            ).first().status,
            "failure",
            "AuditLogStatus must also persist alongside the AuditLog "
            "row in the simple path. This is the row that 6.10b "
            "asserts goes away under outer rollback.",
        )

    # -- 6.10b: rollback also wipes the AuditLogStatus child row ------
    @unittest.expectedFailure  # BUG-001b: outer rollback wipes both AuditLog AND its AuditLogStatus child
    def test_6_10b_audit_status_row_also_survives_outer_rollback(self) -> None:
        """
        Scenario 6.10b — second face of BUG-001b.

        ``ensure_terminal_calculation_audit`` writes TWO rows: an
        ``AuditLog`` parent and an ``AuditLogStatus`` child carrying
        the ``status`` ('success' / 'failure') and the ``error_traceback``.
        The customer's actual diagnostic — the traceback — lives on
        the *child* row, not the parent.

        6.10 only asserts the ``AuditLog`` parent survives. This test
        pins the stronger contract that the ``AuditLogStatus`` child
        also survives — because losing the child means the operator
        knows "something happened" but not "what failed and why".

        Customer story: an operator opens the audit timeline, sees
        an audit entry for a calc that ran, but the status field is
        empty and the traceback drawer is blank. The audit row is a
        ghost — it points at a calc that crashed but carries no
        evidence of *how* it crashed.
        """
        from django.db import transaction

        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10b")
        marker_traceback = "Traceback (BUG-001b 6.10b):\n  RuntimeError: needle"

        try:
            with transaction.atomic():
                ensure_terminal_calculation_audit(
                    calc,
                    audit_status="failure",
                    error_message="6.10b",
                    stack_trace=marker_traceback,
                    context_data={"calculation_id": f"calc_{calc.pk}"},
                )
                raise RuntimeError("outer rollback")
        except RuntimeError:
            pass

        log_rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        self.assertEqual(
            log_rows.count(), 1,
            "Parent AuditLog row must survive — see 6.10.",
        )
        status_rows = AuditLogStatus.objects.filter(audit_log=log_rows.first())
        self.assertEqual(
            status_rows.count(), 1,
            "AuditLogStatus child row must also survive the outer "
            "rollback — losing it leaves a ghost audit entry with "
            "no status and no traceback. Got %d." % status_rows.count(),
        )
        self.assertIn(
            "needle", (status_rows.first().error_traceback or ""),
            "The traceback that ships in AuditLogStatus.error_traceback "
            "is the operator's only diagnostic. Losing it on rollback "
            "defeats the entire purpose of the audit-survives-rollback "
            "contract.",
        )

    # -- 6.10c: 3 retries → 0 audit rows ------------------------------
    @unittest.expectedFailure  # BUG-001b: every retry inside an outer atomic that rolls back is silently lost
    def test_6_10c_three_retries_inside_rollbacks_lose_all_evidence(self) -> None:
        """
        Scenario 6.10c — customer impact at scale.

        A flaky calculation that fails three times in a row. Each
        attempt runs inside its own outer atomic (the kind a long-
        running pipeline uses to make each iteration restartable).
        Each attempt fires ``ensure_terminal_calculation_audit`` to
        record "we tried, here's why it failed".

        Customer-visible expectation: the audit table now carries
        **3 rows** — three timestamped attempts, each with its own
        traceback. An on-call engineer can pull up the three
        tracebacks side by side and triage.

        Observed under BUG-001b: **0 rows.** All three rolled back
        with their respective outer atomics. The on-call engineer
        sees an empty audit timeline for a calculation that ran
        three times.
        """
        from django.db import transaction

        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10c")

        for attempt in range(3):
            try:
                with transaction.atomic():
                    ensure_terminal_calculation_audit(
                        calc,
                        audit_status="failure",
                        error_message=f"attempt {attempt}",
                        stack_trace=f"Traceback (attempt {attempt}):",
                        context_data={
                            "calculation_id": f"calc_{calc.pk}_a{attempt}",
                        },
                    )
                    raise RuntimeError(f"flaky attempt {attempt}")
            except RuntimeError:
                pass

        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        # The customer expects one terminal-audit row per attempt.
        # (If the framework chooses to consolidate retries into a
        # single row, the assertion can be relaxed to >= 1; either
        # way the bug shows as 0.)
        self.assertGreaterEqual(
            rows.count(), 1,
            "After 3 failed attempts in 3 rolled-back outer atomics, "
            "the audit table must contain at least one surviving row "
            "(ideally one per attempt). Got %d — every attempt was "
            "silently lost. This is the failure mode that turns the "
            "audit table into a 'green when broken' false-negative "
            "for on-call operators." % rows.count(),
        )

    # -- 6.10d: nested savepoints (the calc state machine's actual shape) -
    @unittest.expectedFailure  # BUG-001b: savepoint rollback inside an outer atomic also wipes the audit
    def test_6_10d_nested_atomic_savepoint_rollback_loses_audit(self) -> None:
        """
        Scenario 6.10d — the shape the calculation state machine
        actually uses.

        ``CalculationModel.save()`` runs the calc inside a nested
        ``transaction.atomic()`` (savepoint, not a separate
        connection). The customer-facing call site is therefore:

            with transaction.atomic():       # outer (e.g. request)
                with transaction.atomic():   # savepoint (calc body)
                    ensure_terminal_calculation_audit(..., 'failure', ...)
                    raise RuntimeError(...)  # calc fails

        Django releases the savepoint on exception, then propagates
        the error to the outer block which also rolls back. The
        audit row is wiped at the savepoint boundary AND would be
        wiped again at the outer rollback if it had survived.

        This test reproduces that exact nesting shape. If the audit
        is to survive the calc state machine in production, it must
        survive this exact pattern — not just the simpler single-
        atomic shape covered by 6.10.
        """
        from django.db import transaction

        AuditLog.objects.all().delete()
        calc = AuditAtomicCalc.objects.create(name="c6-10d")

        try:
            with transaction.atomic():           # outer (request scope)
                try:
                    with transaction.atomic():   # savepoint (calc body)
                        ensure_terminal_calculation_audit(
                            calc,
                            audit_status="failure",
                            error_message="6.10d",
                            stack_trace="Traceback (nested savepoint):",
                            context_data={
                                "calculation_id": f"calc_{calc.pk}",
                            },
                        )
                        raise RuntimeError("calc body failed")
                except RuntimeError:
                    # Savepoint is rolled back here. Outer atomic is
                    # still alive — let it commit normally. If the
                    # audit had a separate connection it would survive
                    # both the savepoint rollback and any subsequent
                    # outer commit/rollback.
                    pass
        except Exception:  # pragma: no cover — outer commits cleanly
            pass

        self.assertEqual(
            AuditLog.objects.filter(
                resource="auditatomiccalc", object_id=calc.pk,
            ).count(),
            1,
            "The savepoint rollback inside an enclosing atomic must "
            "NOT wipe the terminal audit row — that is the exact "
            "nesting shape that CalculationModel.save() uses, and "
            "the docs promise the audit survives any rollback.",
        )

    # -- 6.10e: real end-to-end through calc.save() -------------------
    @unittest.skip(
        "Blocked by audit-log API-only contract. AuditLogMixin only "
        "fires on REST API requests; a programmatic calc.save() does "
        "NOT seed _pending_terminal_audit, so the except-branch "
        "_finalize_pending_terminal_audit() has nothing to finalize. "
        "A 0-row outcome here is therefore ambiguous: it could be "
        "BUG-001b (row written then rolled back) OR 'no row was ever "
        "written'. The test can only validate BUG-001b once BUG-009 "
        "is fixed (PATCH of is_calculated must accept the field), so "
        "the API path can drive the calc end-to-end with a real "
        "AuditLogMixin-seeded pending row in scope. See 6.10/6.10b/"
        "6.10c/6.10d for the same contract pinned synthetically by "
        "calling ensure_terminal_calculation_audit() directly."
    )
    def test_6_10e_real_failed_atomic_calc_save_loses_audit(self) -> None:
        """
        Scenario 6.10e — BUG-001b reproduced through the *real*
        customer code path.

        In production, DRF's ``ATOMIC_REQUESTS`` middleware wraps
        every request handler in a ``transaction.atomic()``. The
        ``calc.save()`` call (and its hooks) runs *inside* that
        request-scoped atomic. When the calc crashes, the hooks
        write the audit row, then the exception propagates out to
        the request-scoped atomic which rolls back — and takes the
        audit row with it.

        We replicate that production shape by wrapping the
        ``calc.save()`` trigger in an explicit outer atomic (the
        role the DRF request middleware plays). Without it, under
        ``TransactionTestCase``, the audit row commits normally
        (no outer atomic to roll it back) and the test falsely
        passes.

        Path traced through the framework (May 2026):

          1. Outer atomic opens (request-scoped middleware).
          2. ``calc.is_calculated = IN_PROGRESS; calc.save()``
             enters ``LexModel.save()``'s inner atomic (savepoint).
          3. ``AFTER_UPDATE`` hook fires ``calculate_hook`` →
             ``execute_calculation_sync()``.
          4. ``execute_calculation_sync`` opens its own savepoint:
             ``with transaction.atomic(): func()`` (line 453 of
             ``CalculationModel.py``). ``func()`` raises.
          5. Savepoint rolls back. Exception propagates through
             ``LexModel.save()``'s except branch.
          6. ``_finalize_pending_terminal_audit()`` writes the audit
             row — still inside the outer atomic.
          7. Exception re-raised → outer atomic rolls back →
             audit row wiped.
        """
        from django.db import transaction

        AuditLog.objects.all().delete()

        calc = AuditAtomicCalc.objects.create(
            name="c6-10e", should_fail=True,
        )

        # Simulate DRF's ATOMIC_REQUESTS middleware: wrap the entire
        # request-like operation in an outer atomic.
        try:
            with transaction.atomic():
                calc.is_calculated = calc.IN_PROGRESS
                calc.save()
        except Exception:
            pass

        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc.pk,
        )
        self.assertGreaterEqual(
            rows.count(), 1,
            "After a real failed atomic calc.save() inside a "
            "request-scoped outer atomic, at least one terminal "
            "AuditLog row must exist — got %d. The framework writes "
            "the audit row inside the outer atomic, which then "
            "rolls back and takes the row with it (BUG-001b)."
            % rows.count(),
        )
        log = rows.order_by("-id").first()
        status_row = AuditLogStatus.objects.filter(audit_log=log).first()
        self.assertEqual(
            getattr(status_row, "status", None), "failure",
            "Terminal audit for a failed calc must carry "
            "status='failure'. Got %r." % (
                getattr(status_row, "status", None),
            ),
        )

    # -- 6.10f: API PATCH end-to-end -----------------------------------
    @unittest.skip(
        "Blocked by BUG-009. The customer-realistic API trigger for a "
        "calculation is PATCH /api/<calc>/<pk>/ with "
        "{'is_calculated': 'IN_PROGRESS'}, but is_calculated is "
        "declared editable=False on CalculationModel, so DRF silently "
        "drops the field from the PATCH payload and the calc never "
        "fires. Falling back to a programmatic calc.save() doesn't "
        "test BUG-001b either — see 6.10e's skip note. This test "
        "becomes valid (and the right shape) once BUG-009 is fixed: "
        "the PATCH will drive the calc through the API-seeded "
        "AuditLogMixin pending row, and the rollback question becomes "
        "answerable. Until then, BUG-001b is pinned by 6.10/6.10b/"
        "6.10c/6.10d (synthetic) and the conceptual customer story."
    )
    def test_6_10f_api_patch_trigger_failed_calc_loses_audit(self) -> None:
        """
        Scenario 6.10f — BUG-001b reproduced through the REST API.

        Customer journey (the way an operator or frontend actually
        triggers a calculation):

          1. ``POST /api/auditatomiccalc/create/`` → creates the calc
             record with ``should_fail=True``.
          2. ``PATCH /api/auditatomiccalc/<pk>/`` with
             ``{"is_calculated": "IN_PROGRESS"}`` → the documented
             API trigger.

        Note: ``is_calculated`` is declared ``editable=False`` on the
        model (BUG-009), which means DRF silently drops it from the
        PATCH payload. The calc never fires *at all* via API.

        Since BUG-009 blocks the API trigger, we fall back to
        triggering the calc via the model layer inside an explicit
        ``transaction.atomic()`` (simulating DRF's
        ``ATOMIC_REQUESTS`` middleware) so we test BUG-001b
        regardless.

        When BOTH BUG-009 and BUG-001b are fixed, this test can be
        simplified to just the PATCH + assertion — the fallback
        goes away.
        """
        from django.db import transaction
        from rest_framework import status as http_status

        AuditLog.objects.all().delete()

        # Step 1 — create the calc via API (exactly as a customer would)
        resp_create = self.client.post(
            self.url_create(AUDIT_ATOMIC),
            data={"name": "c6-10f-api", "should_fail": True},
            format="json",
        )
        self.assertIn(
            resp_create.status_code,
            (http_status.HTTP_200_OK, http_status.HTTP_201_CREATED),
            "POST create must succeed. Got %d: %r"
            % (resp_create.status_code, getattr(resp_create, "data", None)),
        )
        calc_pk = resp_create.data.get("id")
        self.assertIsNotNone(calc_pk, "Response must include the new calc's id")

        # Step 2 — PATCH is_calculated = IN_PROGRESS via API
        resp_patch = self.client.patch(
            self.url_detail(AUDIT_ATOMIC, calc_pk),
            data={"is_calculated": "IN_PROGRESS"},
            format="json",
        )

        # Step 3 (fallback) — if the PATCH didn't trigger the calc
        # (BUG-009), drive it via model layer inside an outer atomic
        # (simulating DRF's ATOMIC_REQUESTS middleware).
        calc = AuditAtomicCalc.objects.get(pk=calc_pk)
        if calc.is_calculated != "ERROR":
            try:
                with transaction.atomic():
                    calc.is_calculated = "IN_PROGRESS"
                    calc.save()
            except Exception:
                pass

        # The customer-visible question: is there an audit row?
        rows = AuditLog.objects.filter(
            resource="auditatomiccalc", object_id=calc_pk,
        )
        self.assertGreaterEqual(
            rows.count(), 1,
            "After a failed calculation triggered via the API (or "
            "fallback model .save() inside a request-scoped atomic), "
            "at least one terminal AuditLog row must survive. Got "
            "%d. This is BUG-001b in the API layer: the operator "
            "sees zero evidence the calc was attempted." % rows.count(),
        )
        log = rows.order_by("-id").first()
        status_row = AuditLogStatus.objects.filter(audit_log=log).first()
        self.assertEqual(
            getattr(status_row, "status", None), "failure",
            "Terminal audit must carry status='failure' for a calc "
            "that crashed. Got %r." % (
                getattr(status_row, "status", None),
            ),
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




