"""
Cluster 6i: Calculation triggered through the REST API → AuditLog rows.

This is the *real* customer entry point for calculations: a PATCH to
``/api/.../<pk>/`` carrying ``calculate=true`` on a ``CalculationModel``
record. ``OneModelEntry.update`` (see ``lex/api/views/model_entries/One.py``)

    1. flips ``is_calculated`` to ``IN_PROGRESS`` and saves with
       ``skip_hooks=True``,
    2. registers the calc in ``ActiveCalculationStateStore`` and broadcasts
       the IN_PROGRESS WebSocket message,
    3. wraps the body in ``transaction.atomic()`` *only* when
       ``should_use_atomic_model_operations(model_class)`` is true (i.e.
       the model does NOT set ``is_atomic = False``),
    4. calls ``UpdateModelMixin.update`` which routes through
       ``AuditLogMixin.perform_update`` (writes the request-level audit)
       and ultimately fires ``calculate_hook`` (writes the terminal
       calculation audit via ``ensure_terminal_calculation_audit``).

Customer-visible contract this file pins down:

    * **Success path** (atomic *and* non-atomic) → at least one
      ``AuditLog`` row tied to the calc record, with a final
      ``AuditLogStatus.status = 'success'``.
    * **Failure path** (atomic *and* non-atomic) → at least one
      ``AuditLog`` row tied to the calc record, with a final
      ``AuditLogStatus.status = 'failure'`` and a non-empty traceback
      attached. Even when the API view returns HTTP 500, the operator
      must still be able to open the audit timeline and see *why* the
      calculation failed.

Why we do not assert "audit survives outer atomic rollback" here:
the framework only writes audit rows through the REST/CRUD layer
(``AuditLogMixin`` on save, ``ensure_terminal_calculation_audit`` from
``CalculationModel.save`` / ``calculate_hook``). Customers never
hand-drive the terminal-audit writer from inside their own
``transaction.atomic()`` block, so we don't pretend they do.
"""

from __future__ import annotations

import unittest

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    AUDIT_ATOMIC,
    AUDIT_NONATOMIC,
    AuditAtomicCalc,
    AuditNonAtomicCalc,
)


class _CalcAuditViaAPIMixin:
    """Shared assertions for the four (atomic × success/failure) cases.

    Subclasses bind ``model_cls`` and ``url_name`` and inherit the two
    canonical scenarios. Keeping them in a mixin (a) avoids the four
    near-duplicate copy-pastes the cluster would otherwise grow, and
    (b) makes the contract easier to read: if the assertions ever
    diverge between atomic and non-atomic, that divergence will show
    up here as separate methods.
    """

    model_cls = None  # type: ignore[assignment]
    url_name: str = ""

    # -- success ------------------------------------------------------
    def _assert_success_audit_present(self, calc) -> None:
        """At least one AuditLog row for the calc resource, finalized to
        ``success``. We don't lock the count because the request path
        writes both an ``update`` row (AuditLogMixin) and a calculation
        row (terminal audit), and the framework is allowed to evolve
        that ratio — what the customer sees is "the calc shows up in
        the timeline as having run successfully"."""
        rows = AuditLog.objects.filter(
            resource=self.url_name, object_id=calc.pk,
        )
        self.assertGreaterEqual(
            rows.count(), 1,
            f"A successful API-triggered calculation on "
            f"{self.url_name!r} must produce at least one AuditLog "
            f"row tied to the calc record; got {rows.count()}.",
        )
        statuses = list(
            AuditLogStatus.objects.filter(audit_log__in=rows).values_list(
                "status", flat=True,
            )
        )
        self.assertIn(
            "success", statuses,
            f"At least one AuditLogStatus tied to the calc record "
            f"must be 'success' after a successful API-triggered "
            f"calculation; got {statuses!r}.",
        )
        self.assertNotIn(
            "failure", statuses,
            f"No AuditLogStatus may be 'failure' for a successful "
            f"calculation; got {statuses!r}.",
        )

    def _trigger_calc_via_api(self, calc):
        """PATCH the detail endpoint with ``calculate=true`` exactly
        the way the frontend does."""
        return self.client.patch(
            self.url_detail(self.url_name, calc.pk),
            data={"calculate": "true"}, format="json",
        )

    def _run_success_scenario(self) -> None:
        AuditLog.objects.all().delete()
        calc = self.model_cls.objects.create(name="ok", should_fail=False)

        resp = self._trigger_calc_via_api(calc)
        self.assertIn(
            resp.status_code, (200, 201),
            f"A successful calc trigger must return 2xx; got "
            f"{resp.status_code} body={getattr(resp, 'data', None)!r}",
        )
        self._assert_success_audit_present(calc)

    # -- failure ------------------------------------------------------
    def _assert_failure_audit_present(self, calc) -> None:
        """At least one AuditLog row for the calc resource, finalized
        to ``failure``, with a traceback the operator can read.

        The HTTP response is 500 (``APIException`` from
        ``OneModelEntry.update``'s ``CalculationModelException`` /
        generic ``Exception`` branches), but the audit row is the
        permanent record — that is the whole point of the
        audit-log subsystem."""
        rows = AuditLog.objects.filter(
            resource=self.url_name, object_id=calc.pk,
        )
        self.assertGreaterEqual(
            rows.count(), 1,
            f"A failed API-triggered calculation on {self.url_name!r} "
            f"must still produce at least one AuditLog row — losing "
            f"the audit on failure is the failure mode the audit "
            f"subsystem exists to prevent. Got {rows.count()}.",
        )
        status_rows = list(
            AuditLogStatus.objects.filter(audit_log__in=rows)
        )
        statuses = [s.status for s in status_rows]
        self.assertIn(
            "failure", statuses,
            f"At least one AuditLogStatus tied to the failed calc "
            f"must be 'failure'; got {statuses!r}.",
        )
        # The traceback is the operator's only diagnostic — without it
        # the audit row is a ghost ("something happened, no idea what").
        tracebacks = [
            (s.error_traceback or "")
            for s in status_rows
            if s.status == "failure"
        ]
        self.assertTrue(
            any(tracebacks),
            f"At least one failure AuditLogStatus must carry a "
            f"non-empty error_traceback so the operator can diagnose "
            f"the failure after the fact; got {tracebacks!r}.",
        )

    def _run_failure_scenario(self) -> None:
        AuditLog.objects.all().delete()
        calc = self.model_cls.objects.create(name="boom", should_fail=True)

        resp = self._trigger_calc_via_api(calc)
        # The view wraps CalculationModelException / generic Exception
        # in APIException → DRF emits 500. Some failure shapes (e.g.
        # ValidationError reflowed) come back as 400. Anything in the
        # 4xx/5xx band is a "failed call" from the customer's
        # perspective; what we care about is the audit row.
        self.assertGreaterEqual(
            resp.status_code, 400,
            f"A calc that raised in calculate() must surface as a "
            f"4xx/5xx response; got {resp.status_code}.",
        )
        self._assert_failure_audit_present(calc)


class TestCluster06i_AtomicCalcViaAPI(_CalcAuditViaAPIMixin, E2ETestCase):
    """Atomic calc (``is_atomic = True``, the default) triggered via PATCH."""

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    # Let the real audit + cache writers run so we can observe rows.
    e2e_unpatch = {
        "store_message",
        "build_cache_key",
        "ensure_terminal_calculation_audit",
    }

    model_cls = AuditAtomicCalc
    url_name = AUDIT_ATOMIC

    def test_6_50_atomic_calc_success_writes_audit(self) -> None:
        """Scenario 6.50: PATCH ?calculate=true on an atomic calc that
        runs cleanly → AuditLog row(s) with a ``success`` status.

        Atomic models go through ``transaction.atomic()`` in
        ``OneModelEntry.update``; the audit row must commit with the
        successful transaction."""
        self._run_success_scenario()

    def test_6_51_atomic_calc_failure_writes_audit(self) -> None:
        """Scenario 6.51: PATCH ?calculate=true on an atomic calc whose
        ``calculate()`` raises → failure AuditLog with traceback.

        This is the most-asked-about contract for the audit
        subsystem: when an atomic calc blows up, the *audit* of that
        blow-up must remain. Without it, on-call operators see a
        green audit timeline for a calc that never produced output."""
        self._run_failure_scenario()


class TestCluster06i_NonAtomicCalcViaAPI(_CalcAuditViaAPIMixin, E2ETestCase):
    """Non-atomic calc (``is_atomic = False``) triggered via PATCH.

    Non-atomic models opt out of the surrounding ``transaction.atomic()``
    block — see ``should_use_atomic_model_operations`` in
    ``lex/core/models/LexModel.py`` and the call site in
    ``OneModelEntry.create`` / ``.update`` in
    ``lex/api/views/model_entries/One.py``. The audit contract must
    not depend on that wrapping: success and failure both write the
    same audit rows the atomic flavor does.
    """

    e2e_models = ALL_MODELS
    e2e_framework_models = [AuditLog, AuditLogStatus]
    e2e_unpatch = {
        "store_message",
        "build_cache_key",
        "ensure_terminal_calculation_audit",
    }

    model_cls = AuditNonAtomicCalc
    url_name = AUDIT_NONATOMIC

    def test_6_52_non_atomic_calc_success_writes_audit(self) -> None:
        """Scenario 6.52: Non-atomic calc, success path. No outer
        atomic block, so the audit rows commit incrementally — the
        customer-visible result is identical to 6.50."""
        self._run_success_scenario()

    def test_6_53_non_atomic_calc_failure_writes_audit(self) -> None:
        """Scenario 6.53: Non-atomic calc, failure path.

        With ``is_atomic = False`` there is no outer transaction to
        roll back, so any audit row the framework wrote before the
        ``calculate()`` raise survives trivially. This pins that the
        framework still writes the failure audit (terminal audit
        flushed via ``_pending_terminal_audit`` on the error save)
        rather than relying on the absence of a rollback to "save"
        an earlier success row."""
        self._run_failure_scenario()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

