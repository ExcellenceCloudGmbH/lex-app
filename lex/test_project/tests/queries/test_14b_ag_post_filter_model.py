"""
Cluster 14b: AG Grid POST — flat leaf + filterModel / sortModel.

Drives ``POST /api/model_entries/queryitem/list`` with a realistic
AG Grid payload and asserts on the ``rowData`` / ``rowCount``
response.

Method coverage added by this file:

* ``ListModelEntries.post``
* ``ListModelEntries._execute_ag_grid_request`` (routing)
* ``ListModelEntries._normalize_ag_request``
* ``ListModelEntries._apply_filter_model`` + ``_build_filter_q``
  (text / number / date / set / compound OR branches)
* ``ListModelEntries._apply_sort_model``
* ``ListModelEntries._execute_leaf_level``
* ``_parse_ag_date``, ``_parse_ag_datetime``, ``_ag_filter_has_time``
* ``_parse_bool`` (``pivotMode`` False branch)
* ``_parse_int`` (``startRow`` / ``endRow``)

Scenario numbering matches
docs/test-plan/test-clusters.md#14-ag-grid-query-endpoint.
"""

from __future__ import annotations

import unittest
from datetime import date, datetime, timezone
from decimal import Decimal

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
)


def _base_ag_request(**overrides) -> dict:
    """Minimal AG Grid request body — override any subset of keys."""
    req = {
        "startRow": 0,
        "endRow": 100,
        "rowGroupCols": [],
        "groupKeys": [],
        "pivotCols": [],
        "pivotMode": False,
        "valueCols": [],
        "sortModel": [],
        "filterModel": {},
    }
    req.update(overrides)
    return req


class TestCluster14b_AGFlatAndFilterModel(E2ETestCase):
    """AG POST leaf rows with text/number/date/set/compound filters."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.cat_alpha = QueryCategory.objects.create(name="alpha")
        self.cat_beta = QueryCategory.objects.create(name="beta")
        # Fixed fixture set — every 14b scenario reads from the same data.
        self.rows = [
            QueryItem.objects.create(
                name="alpha-small", amount=Decimal("10.00"), count=1,
                status=QUERY_STATUS_ACTIVE, created_on=date(2026, 1, 15),
                created_at_ts=datetime(2026, 1, 15, 10, 0, tzinfo=timezone.utc),
                category=self.cat_alpha,
            ),
            QueryItem.objects.create(
                name="alpha-mid", amount=Decimal("250.00"), count=5,
                status=QUERY_STATUS_ARCHIVED, created_on=date(2026, 3, 10),
                created_at_ts=datetime(2026, 3, 10, 13, 30, 45, tzinfo=timezone.utc),
                category=self.cat_alpha,
            ),
            QueryItem.objects.create(
                name="alpha-big", amount=Decimal("900.00"), count=10,
                status=QUERY_STATUS_ACTIVE, created_on=date(2026, 6, 1),
                created_at_ts=datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc),
                category=self.cat_alpha,
            ),
            QueryItem.objects.create(
                name="beta-mid", amount=Decimal("500.00"), count=0,
                status=QUERY_STATUS_DRAFT, created_on=date(2026, 2, 20),
                created_at_ts=datetime(2026, 2, 20, 14, 0, tzinfo=timezone.utc),
                category=self.cat_beta,
            ),
            QueryItem.objects.create(
                name="skip-me", amount=Decimal("1000.00"), count=99,
                status=QUERY_STATUS_ARCHIVED, created_on=date(2026, 4, 1),
                created_at_ts=datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc),
                category=self.cat_beta,
            ),
        ]

    def _post(self, ag_request: dict):
        return self.client.post(
            self.url_list(ITEM),
            data={"request": ag_request},
            format="json",
        )

    def _names_from(self, resp) -> set[str]:
        rows = resp.data.get("rowData") or []
        return {r.get("name") for r in rows if isinstance(r, dict)}

    # -- 14.8 ----------------------------------------------------------
    def test_14_8_flat_leaf_pagination_slice(self) -> None:
        """Scenario 14.8: ``startRow: 0, endRow: 2`` → ``rowData`` has 2
        rows; ``rowCount`` matches DB total (5)."""
        resp = self._post(_base_ag_request(startRow=0, endRow=2))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertEqual(
            len(resp.data.get("rowData") or []), 2,
            f"Expected 2 paged rows; got {resp.data!r}",
        )
        self.assertEqual(
            resp.data.get("rowCount"), 5,
            f"rowCount must report DB total; got {resp.data.get('rowCount')!r}",
        )

    # -- 14.9 ----------------------------------------------------------
    def test_14_9_filter_model_text_contains(self) -> None:
        """Scenario 14.9: ``filterModel.text.contains`` → only substring matches."""
        req = _base_ag_request(
            filterModel={
                "name": {"filterType": "text", "type": "contains", "filter": "alp"},
            },
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self._names_from(resp),
            {"alpha-small", "alpha-mid", "alpha-big"},
            f"text.contains filter failed; got {self._names_from(resp)!r}",
        )

    # -- 14.10 ---------------------------------------------------------
    def test_14_10_filter_number_inrange_plus_sort_desc(self) -> None:
        """Scenario 14.10: number ``inRange`` + DESC sort — both applied."""
        req = _base_ag_request(
            filterModel={
                "amount": {
                    "filterType": "number", "type": "inRange",
                    "filter": 100, "filterTo": 600,
                },
            },
            sortModel=[{"colId": "amount", "sort": "desc"}],
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("rowData") or []
        amounts = [Decimal(str(r["amount"])) for r in rows]
        self.assertEqual(
            set(r["name"] for r in rows),
            {"alpha-mid", "beta-mid"},
            f"inRange filter returned wrong set; got {rows!r}",
        )
        self.assertEqual(
            amounts, sorted(amounts, reverse=True),
            f"Rows must be DESC-sorted by amount; got {amounts!r}",
        )

    # -- 14.11 ---------------------------------------------------------
    def test_14_11_filter_date_greater_than_datefield(self) -> None:
        """Scenario 14.11: ``DateField`` + ``date`` ``greaterThan`` —
        ``_parse_ag_date`` → ``__gt`` lookup."""
        req = _base_ag_request(
            filterModel={
                "created_on": {
                    "filterType": "date", "type": "greaterThan",
                    "dateFrom": "2026-03-01",
                },
            },
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self._names_from(resp),
            {"alpha-mid", "alpha-big", "skip-me"},
            f"date.greaterThan failed; got {self._names_from(resp)!r}",
        )

    # -- 14.12 ---------------------------------------------------------
    def test_14_12_filter_date_equals_datetime_with_time(self) -> None:
        """Scenario 14.12: ``DateTimeField`` + ``date`` ``equals`` with an
        explicit time component.

        ``_ag_filter_has_time`` must detect the time, routing to the
        second-precision ``__gte / __lt`` window branch in
        ``_build_filter_q``.
        """
        req = _base_ag_request(
            filterModel={
                "created_at_ts": {
                    "filterType": "date", "type": "equals",
                    # Explicit ``Z`` so ``_parse_ag_datetime`` parses
                    # as UTC regardless of the server's current TZ.
                    "dateFrom": "2026-03-10T13:30:45Z",
                },
            },
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self._names_from(resp), {"alpha-mid"},
            f"datetime equals-with-time must match exactly one row; "
            f"got {self._names_from(resp)!r}",
        )

    # -- 14.13 ---------------------------------------------------------
    def test_14_13_filter_set_in_membership(self) -> None:
        """Scenario 14.13: ``filterType: set`` → ``__in`` against values."""
        req = _base_ag_request(
            filterModel={
                "status": {
                    "filterType": "set",
                    "values": [QUERY_STATUS_ACTIVE, QUERY_STATUS_DRAFT],
                },
            },
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self._names_from(resp),
            {"alpha-small", "alpha-big", "beta-mid"},
            f"set filter failed; got {self._names_from(resp)!r}",
        )

    # -- 14.14 ---------------------------------------------------------
    def test_14_14_filter_compound_operator_or(self) -> None:
        """Scenario 14.14: compound filter with ``operator: OR`` — rows
        matching EITHER leg are returned; ``_build_filter_q`` recurses
        through ``conditions``.
        """
        req = _base_ag_request(
            filterModel={
                "name": {
                    "filterType": "text",
                    "operator": "OR",
                    "conditions": [
                        {"filterType": "text", "type": "contains", "filter": "alp"},
                        {"filterType": "text", "type": "contains", "filter": "skip"},
                    ],
                },
            },
        )
        resp = self._post(req)
        self.assertEqual(resp.status_code, 200, resp.data)
        self.assertEqual(
            self._names_from(resp),
            {"alpha-small", "alpha-mid", "alpha-big", "skip-me"},
            f"OR-compound filter failed; got {self._names_from(resp)!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


