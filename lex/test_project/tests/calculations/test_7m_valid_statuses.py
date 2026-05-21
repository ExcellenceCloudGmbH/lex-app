"""
Cluster 7m: ``is_calculated`` status value integrity.

Intent (from docs/features/calculations/ + issue #60):

    The ``is_calculated`` field on a ``CalculationModel`` must always hold
    one of the five documented status values:

        IN_PROGRESS, SUCCESS, ERROR, NOT_CALCULATED, ABORTED

    The string ``"No"`` (and any other invalid status) must never appear in
    the frontend.  This sub-cluster is the regression gate for issue #60
    ("Sometimes I receive 'No' in my calculation status").

    The framework guarantees this invariant through two mechanisms:

      1. **Model-level**: ``editable=False`` + ``choices=STATUSES`` so only
         the five constants are accepted by the ORM's validation layer.
      2. **REST-layer normalization**: every PATCH that does **not** carry
         ``calculate=true`` resets ``is_calculated`` to ``NOT_CALCULATED``
         before persisting (see ``One._prepare_update_request``).

    These tests exercise every observable surface — ORM state directly after
    save, GET response body, and PATCH response body — and confirm that none
    of them can carry an out-of-set value.

Scenario range: 7.155 – 7.160.
"""

from __future__ import annotations

from lex.core.models.CalculationModel import CalculationModel
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, AtomicCalc

# The complete set of statuses the framework documents and guarantees.
_VALID_STATUSES = frozenset({
    CalculationModel.IN_PROGRESS,
    CalculationModel.SUCCESS,
    CalculationModel.ERROR,
    CalculationModel.NOT_CALCULATED,
    CalculationModel.ABORTED,
})

_MODEL_NAME = AtomicCalc._meta.model_name


class TestCluster07m_ValidStatuses(E2ETestCase):
    """
    Cluster 7m: ``is_calculated`` is always one of the five documented
    status constants — never ``"No"`` or any other out-of-set string.
    """

    e2e_models = ALL_MODELS

    # -- 7.155 ------------------------------------------------------------

    def test_7_155_new_record_default_status_is_valid(self) -> None:
        """
        Scenario 7.155: A freshly created record defaults to NOT_CALCULATED.

        Given: A new AtomicCalc instance
        When:  The instance is saved with no explicit ``is_calculated``
        Then:  ``is_calculated`` is ``NOT_CALCULATED`` — never ``"No"`` or
               any other invalid string.
        """
        calc = AtomicCalc(name="m7-155")
        calc.save()

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.NOT_CALCULATED,
            f"New record must default to NOT_CALCULATED; got {fresh.is_calculated!r}",
        )
        self.assertIn(
            fresh.is_calculated,
            _VALID_STATUSES,
            f"is_calculated must be in the valid status set; got {fresh.is_calculated!r}. "
            f"Valid statuses: {sorted(_VALID_STATUSES)}",
        )

    # -- 7.156 ------------------------------------------------------------

    def test_7_156_successful_calculation_status_is_valid(self) -> None:
        """
        Scenario 7.156: After a successful calculation, ``is_calculated`` is
        ``SUCCESS``.

        Given: An AtomicCalc record with ``should_fail=False``
        When:  The record is saved with ``is_calculated=IN_PROGRESS``
        Then:  The final persisted status is ``SUCCESS`` — one of the five
               documented values (not ``"No"``).
        """
        calc = AtomicCalc(name="m7-156", should_fail=False)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        calc.save()

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.SUCCESS,
            f"Successful calculation must end at SUCCESS; got {fresh.is_calculated!r}",
        )
        self.assertIn(
            fresh.is_calculated,
            _VALID_STATUSES,
            f"Post-calculation is_calculated must be in the valid status set; "
            f"got {fresh.is_calculated!r}",
        )

    # -- 7.157 ------------------------------------------------------------

    def test_7_157_failed_calculation_status_is_valid(self) -> None:
        """
        Scenario 7.157: After a failing calculation, ``is_calculated`` is
        ``ERROR``.

        Given: An AtomicCalc record with ``should_fail=True``
        When:  The record is saved with ``is_calculated=IN_PROGRESS``
        Then:  The final persisted status is ``ERROR`` — one of the five
               documented values (not ``"No"``).
        """
        calc = AtomicCalc(name="m7-157", should_fail=True)
        calc.is_calculated = CalculationModel.IN_PROGRESS
        try:
            calc.save()
        except Exception:
            pass

        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertEqual(
            fresh.is_calculated,
            CalculationModel.ERROR,
            f"Failed calculation must end at ERROR; got {fresh.is_calculated!r}",
        )
        self.assertIn(
            fresh.is_calculated,
            _VALID_STATUSES,
            f"Post-failure is_calculated must be in the valid status set; "
            f"got {fresh.is_calculated!r}",
        )

    # -- 7.158 ------------------------------------------------------------

    def test_7_158_api_get_response_includes_valid_status(self) -> None:
        """
        Scenario 7.158: GET detail API response includes ``is_calculated``
        as a valid status value.

        Given: An AtomicCalc record in NOT_CALCULATED state
        When:  GET /api/<model>/<id>/ is called
        Then:  The response body includes ``is_calculated`` as one of the
               five documented statuses (never ``"No"`` or any other invalid
               string).
        """
        calc = AtomicCalc.objects.create(name="m7-158")
        resp = self.client.get(self.url_detail(_MODEL_NAME, calc.pk))

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
            f"GET detail must return 200; got {resp.status_code}",
        )
        self.assertIn(
            "is_calculated",
            resp.data,
            "GET detail response must include the is_calculated field",
        )
        api_status = resp.data["is_calculated"]
        self.assertIn(
            api_status,
            _VALID_STATUSES,
            f"GET response is_calculated must be a valid status; got {api_status!r}. "
            f"Valid statuses: {sorted(_VALID_STATUSES)}",
        )

    # -- 7.159 ------------------------------------------------------------

    def test_7_159_api_patch_without_calculate_returns_valid_status(self) -> None:
        """
        Scenario 7.159: PATCH without ``calculate=true`` always returns a
        valid ``is_calculated`` in the response body.

        Given: An AtomicCalc record
        When:  PATCH /api/<model>/<id>/ is called with only non-status
               fields (no ``calculate=true``)
        Then:  The response body includes ``is_calculated`` as one of the
               five documented statuses.

        The framework resets ``is_calculated`` to ``NOT_CALCULATED`` on every
        plain PATCH via ``One._prepare_update_request``.
        """
        calc = AtomicCalc.objects.create(name="m7-159-orig")
        resp = self.client.patch(
            self.url_detail(_MODEL_NAME, calc.pk),
            data={"name": "m7-159-updated"},
            format="json",
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
            f"PATCH must return 200; got {resp.status_code}",
        )
        self.assertIn(
            "is_calculated",
            resp.data,
            "PATCH response must include the is_calculated field",
        )
        api_status = resp.data["is_calculated"]
        self.assertIn(
            api_status,
            _VALID_STATUSES,
            f"PATCH response is_calculated must be a valid status; got {api_status!r}. "
            f"Valid statuses: {sorted(_VALID_STATUSES)}",
        )

    # -- 7.160 ------------------------------------------------------------

    def test_7_160_patch_with_invalid_status_does_not_corrupt_record(self) -> None:
        """
        Scenario 7.160: PATCH with ``is_calculated="No"`` does not corrupt
        the stored status.

        Given: An AtomicCalc record in NOT_CALCULATED state
        When:  PATCH /api/<model>/<id>/ is called with
               ``{"is_calculated": "No"}`` in the payload
        Then:  - The API response still shows a valid status (never ``"No"``)
               - The DB record still holds a valid status
               This is the direct regression test for issue #60.
        """
        calc = AtomicCalc.objects.create(name="m7-160")
        resp = self.client.patch(
            self.url_detail(_MODEL_NAME, calc.pk),
            data={"name": "m7-160-patched", "is_calculated": "No"},
            format="json",
        )

        self.assertEqual(
            resp.status_code,
            status.HTTP_200_OK,
            f"PATCH must return 200; got {resp.status_code}",
        )
        self.assertIn(
            "is_calculated",
            resp.data,
            "PATCH response must include the is_calculated field",
        )

        # The API response must never carry "No".
        api_status = resp.data["is_calculated"]
        self.assertNotEqual(
            api_status,
            "No",
            "Framework must never return 'No' as a calculation status in the API response",
        )
        self.assertIn(
            api_status,
            _VALID_STATUSES,
            f"PATCH response is_calculated must be a valid status after "
            f"attempting to set 'No'; got {api_status!r}. "
            f"Valid statuses: {sorted(_VALID_STATUSES)}",
        )

        # The DB record must also be clean.
        fresh = AtomicCalc.objects.get(pk=calc.pk)
        self.assertNotEqual(
            fresh.is_calculated,
            "No",
            "DB record must not store 'No' as a calculation status",
        )
        self.assertIn(
            fresh.is_calculated,
            _VALID_STATUSES,
            f"DB record is_calculated must be a valid status; "
            f"got {fresh.is_calculated!r}. "
            f"Valid statuses: {sorted(_VALID_STATUSES)}",
        )
