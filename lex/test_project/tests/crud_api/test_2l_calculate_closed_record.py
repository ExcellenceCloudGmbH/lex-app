"""A closed record over the REST API: Calculate is refused, the rest stays editable.

Intent: the Calculate button must refuse a closed record before anything
changes, and tell the user why. The PATCH answers 409 with the model's own
reason in ``detail``, which is the key the frontend shows in its error
notification; the record keeps its status, and no run is registered or
announced. Closing stops only the calculation: every other field stays as
editable as it was, and editing a closed record keeps its status, because a
closed record can never be recalculated to set it again. A regression here
either recalculates a record whose result was already relied on, locks a
closed record against ordinary edits, or leaves it stranded in NOT_CALCULATED.
Cluster 2l — scenarios 2.110–2.116. Type: E.
Covers: lex/api/views/model_entries/One.py (``OneModelEntry.update``,
``perform_update``), lex/core/calculation_closing.py.
Run: python -m lex pytest lex/test_project/tests/crud_api/test_2l_calculate_closed_record.py -v
"""

from __future__ import annotations

import time

import pytest
from rest_framework import status

from lex.core.models.CalculationModel import CalculationModel
from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..calculations.models import (
    CLOSABLE,
    CLOSED_BY_TRUE,
    CLOSED_MODELS,
    SAP_POSTED,
    ClosableCalc,
    ClosedByTrueCalc,
)

pytestmark = pytest.mark.crud_api

# The framework's message when the method answers True rather than a reason.
DEFAULT_CLOSED_REASON = "This record is closed, so it can't be calculated again."


class TestCluster02l_CalculateClosedRecord(E2ETestCase):
    """Cluster 2l: Calculate refuses a closed record; its other fields stay editable."""

    e2e_models = CLOSED_MODELS
    e2e_unpatch = {"mark_in_progress"}

    def setUp(self):
        super().setUp()
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

        ActiveCalculationStateStore.clear_all()
        ClosableCalc.calls = 0
        ClosedByTrueCalc.calls = 0

    def tearDown(self):
        from lex.core.signals.ActiveCalculationStateStore import ActiveCalculationStateStore

        ActiveCalculationStateStore.clear_all()
        super().tearDown()

    def _calculate(self, model_name, pk):
        return self.client.patch(self.url_detail(model_name, pk), data={"calculate": "true"}, format="json")

    def _edit(self, pk, **fields):
        """Save ``fields`` the way the edit form does: a PATCH without ``calculate``."""
        return self.client.patch(self.url_detail(CLOSABLE, pk), data=fields, format="json")

    def _calculated(self, *, closed):
        """A record that already calculated: SUCCESS, total 5."""
        record = ClosableCalc.objects.create(name="posted", closed=closed)
        ClosableCalc.objects.filter(pk=record.pk).update(is_calculated=CalculationModel.SUCCESS, total=5)
        record.refresh_from_db()
        return record

    # -- 2.110 ---------------------------------------------------------
    def test_2_110_a_closed_record_is_refused_with_its_reason(self):
        """
        Scenario 2.110: Calculate on a closed record
        Given: a closed record that already calculated (SUCCESS)
        When: the client sends the Calculate PATCH
        Then: 409 with the model's reason in ``detail``; the record stays
              SUCCESS; no run is marked in progress
        """
        record = ClosableCalc.objects.create(name="posted", closed=True)
        ClosableCalc.objects.filter(pk=record.pk).update(is_calculated=CalculationModel.SUCCESS)
        spy = self.spy_on("mark_in_progress")

        resp = self._calculate(CLOSABLE, record.pk)

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, getattr(resp, "data", None))
        self.assertEqual(
            resp.data.get("detail"), SAP_POSTED,
            "The refusal must carry the model's reason in 'detail', which the frontend shows.",
        )
        record.refresh_from_db()
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The status must not change.")
        self.assertFalse(spy.called, "No run may be registered for a closed record.")
        self.assertEqual(ClosableCalc.calls, 0, "calculate() must not run.")

    # -- 2.111 ---------------------------------------------------------
    def test_2_111_an_open_record_is_accepted_as_before(self):
        """
        Scenario 2.111: nothing changes for a record that is open
        Given: a record whose ``calculation_closed_reason()`` answers None
        When: the client sends the Calculate PATCH
        Then: 202, as always
        """
        record = ClosableCalc.objects.create(name="open", closed=False)

        resp = self._calculate(CLOSABLE, record.pk)

        self.assertEqual(resp.status_code, status.HTTP_202_ACCEPTED, getattr(resp, "data", None))
        # Let the background run finish, so it never races the test's teardown.
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            record.refresh_from_db()
            if record.is_calculated != CalculationModel.IN_PROGRESS:
                break
            time.sleep(0.05)
        self.assertEqual(record.is_calculated, CalculationModel.SUCCESS, "The open record's run must finish.")

    # -- 2.112 ---------------------------------------------------------
    def test_2_112_a_yes_without_a_reason_gets_the_default_message(self):
        """
        Scenario 2.112: the method answers True
        Given: a model whose ``calculation_closed_reason()`` answers True
        When: the client sends the Calculate PATCH
        Then: 409 with the framework's default message in ``detail``
        """
        record = ClosedByTrueCalc.objects.create(name="closed")

        resp = self._calculate(CLOSED_BY_TRUE, record.pk)

        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT, getattr(resp, "data", None))
        self.assertEqual(resp.data.get("detail"), DEFAULT_CLOSED_REASON)
        self.assertEqual(ClosedByTrueCalc.calls, 0, "calculate() must not run.")

    # -- 2.113 ---------------------------------------------------------
    def test_2_113_a_closed_record_keeps_its_other_fields_editable(self):
        """
        Scenario 2.113: only the calculation is closed
        Given: a closed record that already calculated (SUCCESS, total 5)
        When: the client edits another of its fields, the way the edit form saves
        Then: 200 and the edit is saved; the record stays closed and keeps
              SUCCESS and its result, since it can never be recalculated
        """
        record = self._calculated(closed=True)

        resp = self._edit(record.pk, note="fixed a typo")

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        record.refresh_from_db()
        self.assertEqual(record.note, "fixed a typo", "A closed record's other fields must stay editable.")
        self.assertTrue(record.closed, "The record must stay closed.")
        self.assertEqual(
            record.is_calculated, CalculationModel.SUCCESS,
            "Editing a closed record must not change its status.",
        )
        self.assertEqual(resp.data.get("is_calculated"), CalculationModel.SUCCESS, "The answer must show the status kept.")
        self.assertEqual(record.total, 5, "The result must not change.")
        self.assertEqual(ClosableCalc.calls, 0, "Nothing may calculate.")

    # -- 2.114 ---------------------------------------------------------
    def test_2_114_closing_a_record_in_the_form_keeps_its_status(self):
        """
        Scenario 2.114: closing a record through an edit
        Given: an open record that already calculated (SUCCESS)
        When: the client sets the field its ``calculation_closed_reason()`` reads
        Then: 200; the record is closed, keeps SUCCESS, and stops offering
              Calculate
        """
        record = self._calculated(closed=False)

        resp = self._edit(record.pk, closed=True)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        record.refresh_from_db()
        self.assertTrue(record.closed)
        self.assertEqual(
            record.is_calculated, CalculationModel.SUCCESS,
            "Closing a record must not change its status: nothing could ever set SUCCESS again.",
        )
        self.assertNotIn(
            "is_calculated", resp.data["lex_reserved_scopes"]["edit"],
            "The closed record must stop offering Calculate.",
        )

    # -- 2.115 ---------------------------------------------------------
    def test_2_115_reopening_a_record_resets_it_like_any_edit(self):
        """
        Scenario 2.115: reopening a record through an edit
        Given: a closed record that calculated (SUCCESS)
        When: the client clears the field its ``calculation_closed_reason()`` reads
        Then: 200; the record is open again, so the edit resets it to
              NOT_CALCULATED like any edit of an open record, and Calculate is
              offered again
        """
        record = self._calculated(closed=True)

        resp = self._edit(record.pk, closed=False)

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        record.refresh_from_db()
        self.assertFalse(record.closed)
        self.assertEqual(record.is_calculated, CalculationModel.NOT_CALCULATED)
        self.assertIn("is_calculated", resp.data["lex_reserved_scopes"]["edit"], "Calculate must be offered again.")

    # -- 2.116 ---------------------------------------------------------
    def test_2_116_an_open_record_is_still_reset_by_an_edit(self):
        """
        Scenario 2.116: nothing changes for open records
        Given: an open record that calculated (SUCCESS)
        When: the client edits one of its fields
        Then: 200; the edit resets it to NOT_CALCULATED, as before
        """
        record = self._calculated(closed=False)

        resp = self._edit(record.pk, note="new input")

        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        record.refresh_from_db()
        self.assertEqual(record.note, "new input")
        self.assertEqual(record.is_calculated, CalculationModel.NOT_CALCULATED)
