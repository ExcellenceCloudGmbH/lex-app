"""A closed record tells the frontend it can't be calculated, and why.

Intent: the frontend enables a record's Calculate button only while the
record's ``lex_reserved_scopes.edit`` lists ``is_calculated``. A closed record
leaves it out, so its button is greyed and nobody has to press it to find out;
everything else the user may edit stays editable. The row also carries the
model's reason, so the greyed button can say why instead of claiming the user
lacks permission. A regression here shows an active Calculate button that can
only ever be refused, or a greyed one that blames the wrong thing.
Cluster 12m — scenarios 12.58–12.62. Type: E.
Covers: lex/api/serializers/base_serializers.py (``get_lex_reserved_scopes``),
lex/core/calculation_closing.py.
Run: python -m lex pytest lex/test_project/tests/serializers/test_12m_closed_record_scopes.py -v
"""

from __future__ import annotations

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from ..calculations.models import (
    CLOSABLE,
    CLOSABLE_NARROW_READ,
    CLOSED_MODELS,
    SAP_POSTED,
    ClosableCalc,
    ClosableNarrowReadCalc,
)

pytestmark = pytest.mark.serializers

REASON_KEY = "lex_reserved_calculation_closed_reason"


def _grid_request():
    return {
        "startRow": 0, "endRow": 100, "rowGroupCols": [], "groupKeys": [], "pivotCols": [],
        "pivotMode": False, "valueCols": [], "sortModel": [], "filterModel": {},
    }


class TestCluster12m_ClosedRecordScopes(E2ETestCase):
    """Cluster 12m: ``is_calculated`` drops out of a closed record's edit scopes."""

    e2e_models = CLOSED_MODELS

    def _detail(self, record):
        resp = self.client.get(self.url_detail(CLOSABLE, record.pk))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        return resp.data

    def _edit_scopes(self, record):
        return self._detail(record)["lex_reserved_scopes"]["edit"]

    def _grid_row(self, record, model_name=CLOSABLE):
        resp = self.client.post(self.url_list(model_name), data={"request": _grid_request()}, format="json")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, getattr(resp, "data", None))
        return {row["id"]: row for row in resp.data["rowData"]}[record.pk]

    # -- 12.58 ---------------------------------------------------------
    def test_12_58_a_closed_record_cannot_be_calculated_from_the_ui(self):
        """
        Scenario 12.58: the Calculate button is greyed for a closed record
        Given: a closed record the user may edit
        When: the frontend reads it
        Then: its edit scopes leave out ``is_calculated``, and still list the
              fields the user may change
        """
        record = ClosableCalc.objects.create(name="posted", closed=True)

        edit = self._edit_scopes(record)

        self.assertNotIn("is_calculated", edit, "A closed record must not offer Calculate.")
        self.assertIn("note", edit, "Closing stops calculation only; other fields stay editable.")

    # -- 12.59 ---------------------------------------------------------
    def test_12_59_an_open_record_still_offers_calculate(self):
        """
        Scenario 12.59: nothing changes while the record is open
        Given: a record whose ``calculation_closed_reason()`` answers None
        When: the frontend reads it
        Then: its edit scopes list ``is_calculated``, so Calculate is enabled
        """
        record = ClosableCalc.objects.create(name="open", closed=False)

        self.assertIn("is_calculated", self._edit_scopes(record))

    # -- 12.60 ---------------------------------------------------------
    def test_12_60_a_closed_record_says_why_on_every_row(self):
        """
        Scenario 12.60: the greyed button can say why
        Given: a closed record
        When: the grid lists it, and the record page reads it
        Then: both carry the model's reason in
              ``lex_reserved_calculation_closed_reason``
        """
        record = ClosableCalc.objects.create(name="posted", closed=True)

        self.assertEqual(self._grid_row(record).get(REASON_KEY), SAP_POSTED, "The grid row must carry the reason.")
        self.assertEqual(self._detail(record).get(REASON_KEY), SAP_POSTED, "The record page must carry the reason.")

    # -- 12.61 ---------------------------------------------------------
    def test_12_61_an_open_record_carries_no_reason(self):
        """
        Scenario 12.61: nothing to explain while the record is open
        Given: a record whose ``calculation_closed_reason()`` answers None
        When: the grid lists it
        Then: the row carries the key with null, so the frontend keeps its
              usual tooltip
        """
        record = ClosableCalc.objects.create(name="open", closed=False)

        row = self._grid_row(record)

        self.assertIn(REASON_KEY, row, "Calculation rows must always carry the key.")
        self.assertIsNone(row[REASON_KEY])

    # -- 12.62 ---------------------------------------------------------
    def test_12_62_the_reason_survives_a_narrow_read_permission(self):
        """
        Scenario 12.62: models that limit which fields a user may read
        Given: a closed record of a model whose users may read only ``name``
               and ``is_calculated``
        When: the grid lists it
        Then: the row still carries the reason, while the unreadable
              ``closed`` field stays out
        """
        record = ClosableNarrowReadCalc.objects.create(name="posted", closed=True)

        row = self._grid_row(record, CLOSABLE_NARROW_READ)

        self.assertNotIn("closed", row, "The read permission must still filter the row.")
        self.assertEqual(row.get(REASON_KEY), SAP_POSTED, "The reason must survive the read filter.")
