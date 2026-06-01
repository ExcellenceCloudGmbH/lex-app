"""
Cluster 10m: REST surface for cancelling a running calculation.

Intent
------
``POST /api/cancel-calculation/<model_name>/<pk>`` is the HTTP-level
entry point a frontend (or any external orchestrator) calls when a user
clicks the Cancel button on an ``IN_PROGRESS`` ``CalculationModel``
record.  The endpoint is the only customer-facing way to reach
:meth:`CalculationModel.request_cancel`; if it is missing, broken, or
silently drops the request, the Cancel button does nothing.

Surfaces covered by this batch:

* **Cancel an active calculation** → 202 Accepted with
  ``status="cancel_requested"`` and the requesting actor; the cancel
  flag is set on the in-memory state store so the running calculation
  can observe it.
* **Cancel a record that is not currently calculating** → 200 OK with
  ``status="not_running"`` (idempotent no-op — protects against
  double-clicks and races with a calc that finished just before the
  user clicked Cancel).
* **Cancel an unknown record** → 404 Not Found.
* **Cancel an unknown model** → 404 Not Found.
* **Cancel a non-``CalculationModel`` row** → 400 Bad Request
  (cancellation is only meaningful for calculation records).
* **Anonymous request** → 401/403 (the endpoint sits behind
  ``IsAuthenticated | HasAPIKey`` so a logged-out caller can't cancel
  another user's work).

Scenario range — **10.61 – 10.67**.

Run
---
``python -m lex pytest lex/test_project/tests/api_layer/test_10m_cancel_calculation_endpoint.py -v``
"""

from __future__ import annotations

import pytest
from django.db import models
from django.urls import reverse
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult
from lex.core.signals.ActiveCalculationStateStore import (
    ActiveCalculationStateStore,
)
from lex.tests.e2e._e2e_test_case import E2ETestCase

pytestmark = pytest.mark.api_layer


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("10m")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("10m")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class CancelEndpointCalc(CalculationModel):
    """Trivial CalculationModel — these tests drive the HTTP layer, not
    the calculation engine, so the body doesn't need to do anything."""

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        # No-op: this batch tests the cancel REST surface, which doesn't
        # run calculate() — it only flags the in-memory store.
        return


@_permissive
class CancelEndpointPlain(LexModel):
    """Plain LexModel (NOT a CalculationModel) used by scenario 10.66 to
    prove the endpoint rejects cancellation requests for record types
    that have no calculation lifecycle."""

    name = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


ALL_10M_MODELS = [CancelEndpointCalc, CancelEndpointPlain]


def _cancel_url(model_name: str, pk) -> str:
    """Reverse the cancel-calculation endpoint URL."""
    return reverse(
        "process_admin_rest_api:cancel-calculation",
        kwargs={"model_name": model_name, "pk": str(pk)},
    )


class TestCluster10m_CancelCalculationEndpoint(E2ETestCase):
    """HTTP-level contract for ``POST /api/cancel-calculation/<model>/<pk>``.

    The default ``mark_in_progress`` mock is opted out so the real
    state-store registration the endpoint depends on is in effect.
    """

    e2e_models = ALL_10M_MODELS
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self) -> None:
        super().setUp()
        ActiveCalculationStateStore.clear_all()
        self.addCleanup(ActiveCalculationStateStore.clear_all)

    # -- 10.61 ---------------------------------------------------------
    def test_10_61_cancel_active_calculation_returns_202(self) -> None:
        """Scenario 10.61: POST against an IN_PROGRESS record → 202.

        Given: a calculation registered as active in the state store
        (the situation that holds while ``calculate()`` is running).
        When: an authenticated user POSTs to the cancel endpoint.
        Then: response is 202 Accepted, body declares
        ``status="cancel_requested"`` and carries the record_id, and
        the cancel flag is set on the store entry — the next
        ``check_cancelled`` poll inside the calculation will trip.
        """
        calc = CancelEndpointCalc.objects.create(name="10m-61")
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-61",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        resp = self.client.post(_cancel_url("cancelendpointcalc", calc.pk))

        self.assertEqual(
            resp.status_code,
            status.HTTP_202_ACCEPTED,
            f"Active-cancel must return 202; got {resp.status_code} {resp.data!r}",
        )
        self.assertEqual(resp.data.get("status"), "cancel_requested")
        self.assertEqual(resp.data.get("record_id"), record_id)
        self.assertTrue(
            ActiveCalculationStateStore.is_cancel_requested(record_id),
            "The endpoint must actually flip the cancel flag on the store, "
            "not just return a hopeful 202.",
        )

    # -- 10.62 ---------------------------------------------------------
    def test_10_62_cancel_idle_record_returns_200_not_running(self) -> None:
        """Scenario 10.62: POST against an idle record → 200 ``not_running``.

        Given: a saved record that is NOT currently calculating (no
        entry in the state store).
        When: the user POSTs cancel.
        Then: response is 200 OK with ``status="not_running"`` — a
        benign no-op so double-clicks and post-completion races don't
        surface a scary error to the user.
        """
        calc = CancelEndpointCalc.objects.create(name="10m-62")

        resp = self.client.post(_cancel_url("cancelendpointcalc", calc.pk))

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
            f"Cancel on idle record must return 200; got {resp.status_code} {resp.data!r}",
        )
        self.assertEqual(resp.data.get("status"), "not_running")

    # -- 10.63 ---------------------------------------------------------
    def test_10_63_unknown_pk_returns_404(self) -> None:
        """Scenario 10.63: POST with an unknown ``pk`` → 404."""
        resp = self.client.post(_cancel_url("cancelendpointcalc", 999999))

        self.assertEqual(
            resp.status_code,
            status.HTTP_404_NOT_FOUND,
            f"Unknown pk must return 404; got {resp.status_code} {resp.data!r}",
        )

    # -- 10.64 ---------------------------------------------------------
    def test_10_64_unknown_model_returns_404(self) -> None:
        """Scenario 10.64: POST with an unknown model name → 404."""
        resp = self.client.post(_cancel_url("totallymadeupmodel", 1))

        self.assertEqual(
            resp.status_code,
            status.HTTP_404_NOT_FOUND,
            f"Unknown model must return 404; got {resp.status_code} {resp.data!r}",
        )

    # -- 10.65 ---------------------------------------------------------
    def test_10_65_non_calculation_model_returns_400(self) -> None:
        """Scenario 10.65: POST against a plain ``LexModel`` → 400.

        Cancellation is only meaningful for ``CalculationModel`` rows —
        attempting to cancel a plain model is a client error, not a
        404 (the model exists) and not a 200 (it would be misleading
        to claim success).
        """
        plain = CancelEndpointPlain.objects.create(name="10m-65")

        resp = self.client.post(_cancel_url("cancelendpointplain", plain.pk))

        self.assertEqual(
            resp.status_code,
            status.HTTP_400_BAD_REQUEST,
            f"Cancel on non-CalculationModel must return 400; got "
            f"{resp.status_code} {resp.data!r}",
        )

    # -- 10.66 ---------------------------------------------------------
    def test_10_66_anonymous_request_is_rejected(self) -> None:
        """Scenario 10.66: anonymous POST is rejected by auth gate.

        The endpoint sits behind ``IsAuthenticated | HasAPIKey``; a
        logged-out caller must NOT be able to cancel another user's
        calculation.  The exact code can vary by middleware
        configuration (401 from DRF, 302/redirect from OIDC, 403 from
        permissions); the contract is "anything in the 3xx/4xx
        rejection band" — never 2xx, never 202.
        """
        calc = CancelEndpointCalc.objects.create(name="10m-66")
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-66",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        # Log out the default e2e session user.
        self.client.logout()
        self.client.cookies.clear()

        resp = self.client.post(_cancel_url("cancelendpointcalc", calc.pk))

        self.assertNotIn(
            resp.status_code,
            (status.HTTP_200_OK, status.HTTP_202_ACCEPTED),
            f"Anonymous cancel must NOT succeed; got status {resp.status_code}",
        )
        self.assertFalse(
            ActiveCalculationStateStore.is_cancel_requested(record_id),
            "Anonymous request must NOT have flipped the cancel flag on the store.",
        )

    # -- 10.67 ---------------------------------------------------------
    def test_10_67_requested_by_recorded_from_session_user(self) -> None:
        """Scenario 10.67: the requesting actor is captured for the audit trail.

        Given: an authenticated session user (``e2e_user``) cancels an
        active calculation.
        When: the endpoint returns 202.
        Then: the response body and the state-store entry both record
        the user as ``requested_by`` — operators need to know who
        cancelled which calculation.
        """
        calc = CancelEndpointCalc.objects.create(name="10m-67")
        record_id = f"{calc._meta.model_name}_{calc.pk}"
        ActiveCalculationStateStore.mark_in_progress(
            record_id=record_id,
            calculation_id="calc-67",
            record=str(calc),
            model_label=calc._meta.label_lower,
            record_pk=calc.pk,
        )

        resp = self.client.post(_cancel_url("cancelendpointcalc", calc.pk))

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED)
        # The default session user is "e2e_user" (see E2ETestCase.setUp).
        self.assertEqual(
            resp.data.get("requested_by"),
            "e2e_user",
            f"Expected requested_by='e2e_user'; got {resp.data!r}",
        )
        self.assertEqual(
            ActiveCalculationStateStore.get_cancel_requested_by(record_id),
            "e2e_user",
        )


