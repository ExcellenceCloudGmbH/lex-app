"""
Cluster 14c: AG Grid POST — grouping, aggregation, pivot + edge cases.

Drives the real grouped / aggregated / pivoted AG Grid responses and
validates that every branch of the AG Grid POST dispatcher produces
the right row set and the right aggregated numbers.

Method coverage added by this file:

* ``_execute_group_level``  (group-row level response shape)
* ``_apply_group_key_filters`` (drill-down into a specific group)
* ``_execute_leaf_level`` under a group drill-down
* ``_execute_pivot_mode``
* ``_build_value_annotations`` + ``_build_agg_expression``
* ``_build_pivot_annotations`` + ``_build_conditional_agg_expression``
* ``_is_valid_field_path`` (silent-drop of unknown field paths)
* ``_parse_columns``

Scenario numbering matches
docs/test-plan/test-clusters.md#14-ag-grid-query-endpoint.
"""

from __future__ import annotations

import unittest
from decimal import Decimal

from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import (
    ALL_MODELS,
    ITEM,
    QUERY_STATUS_ACTIVE,
    QUERY_STATUS_ARCHIVED,
    QUERY_STATUS_DRAFT,
    QueryCategory,
    QueryItem,
)

import pytest

pytestmark = pytest.mark.queries


def _ag(**overrides) -> dict:
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


class TestCluster14c_Grouping(E2ETestCase):
    """Grouped, aggregated, and pivoted AG responses."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.cat_alpha = QueryCategory.objects.create(name="alpha")
        self.cat_beta = QueryCategory.objects.create(name="beta")

        # Alpha category: 3 items totaling 1160 (10+250+900), mixed status.
        self.alpha_small = QueryItem.objects.create(
            name="alpha-small", amount=Decimal("10.00"), count=1,
            status=QUERY_STATUS_ACTIVE, category=self.cat_alpha,
        )
        self.alpha_mid = QueryItem.objects.create(
            name="alpha-mid", amount=Decimal("250.00"), count=5,
            status=QUERY_STATUS_ARCHIVED, category=self.cat_alpha,
        )
        self.alpha_big = QueryItem.objects.create(
            name="alpha-big", amount=Decimal("900.00"), count=10,
            status=QUERY_STATUS_ACTIVE, category=self.cat_alpha,
        )
        # Beta category: 2 items totaling 1500 (500 + 1000).
        self.beta_mid = QueryItem.objects.create(
            name="beta-mid", amount=Decimal("500.00"), count=0,
            status=QUERY_STATUS_DRAFT, category=self.cat_beta,
        )
        self.beta_big = QueryItem.objects.create(
            name="beta-big", amount=Decimal("1000.00"), count=99,
            status=QUERY_STATUS_ARCHIVED, category=self.cat_beta,
        )

    def _post(self, ag_request: dict):
        return self.client.post(
            self.url_list(ITEM),
            data={"request": ag_request},
            format="json",
        )

    # -- 14.15 ---------------------------------------------------------
    def test_14_15_row_group_cols_level_0_returns_group_rows(self) -> None:
        """Scenario 14.15: ``rowGroupCols: [category]`` with no
        ``groupKeys`` → ``rowData`` is one row per category plus
        ``__childCount``; ``_execute_group_level`` runs."""
        resp = self._post(_ag(
            rowGroupCols=[{"id": "category", "field": "category"}],
        ))
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)

        rows = resp.data.get("rowData") or []
        self.assertEqual(
            len(rows), 2,
            f"Group level must return one row per distinct category; got {rows!r}",
        )
        by_cat_pk = {row["category"]: row for row in rows}
        self.assertEqual(
            by_cat_pk[self.cat_alpha.pk]["__childCount"], 3,
            f"alpha childCount must be 3; got {by_cat_pk!r}",
        )
        self.assertEqual(
            by_cat_pk[self.cat_beta.pk]["__childCount"], 2,
            f"beta childCount must be 2; got {by_cat_pk!r}",
        )

    # -- 14.16 ---------------------------------------------------------
    def test_14_16_group_level_value_cols_sum_amount(self) -> None:
        """Scenario 14.16: group by category + valueCols ``amount:sum``
        → each group row carries SUM(amount)."""
        resp = self._post(_ag(
            rowGroupCols=[{"id": "category", "field": "category"}],
            valueCols=[
                {"id": "amount", "field": "amount", "aggFunc": "sum"},
            ],
        ))
        self.assertEqual(resp.status_code, 200, resp.data)

        rows = resp.data.get("rowData") or []
        by_cat_pk = {row["category"]: row for row in rows}

        self.assertEqual(
            Decimal(str(by_cat_pk[self.cat_alpha.pk]["amount"])),
            Decimal("1160.00"),
            f"alpha SUM(amount) must be 1160.00; got {by_cat_pk!r}",
        )
        self.assertEqual(
            Decimal(str(by_cat_pk[self.cat_beta.pk]["amount"])),
            Decimal("1500.00"),
            f"beta SUM(amount) must be 1500.00; got {by_cat_pk!r}",
        )

    # -- 14.17 ---------------------------------------------------------
    def test_14_17_drill_into_group_returns_leaf_rows(self) -> None:
        """Scenario 14.17: drill into the alpha group —
        ``rowGroupCols: [category]`` + ``groupKeys: [alpha.pk]`` →
        ``_apply_group_key_filters`` narrows to alpha leaf rows only."""
        resp = self._post(_ag(
            rowGroupCols=[{"id": "category", "field": "category"}],
            groupKeys=[self.cat_alpha.pk],
        ))
        self.assertEqual(resp.status_code, 200, resp.data)

        rows = resp.data.get("rowData") or []
        names = {r.get("name") for r in rows}
        self.assertEqual(
            names, {"alpha-small", "alpha-mid", "alpha-big"},
            f"Drill-down must return only the alpha leaf rows; got {names!r}",
        )

    # -- 14.18 ---------------------------------------------------------
    def test_14_18_pivot_mode_status_x_amount_sum(self) -> None:
        """Scenario 14.18: pivot ``status`` × ``amount:sum`` — aggregated
        into a single row with one column per distinct ``status`` value.

        Exercises ``_execute_pivot_mode`` → ``_build_pivot_annotations``
        → ``_build_conditional_agg_expression``.
        """
        resp = self._post(_ag(
            pivotMode=True,
            pivotCols=[{"id": "status", "field": "status"}],
            valueCols=[
                {"id": "amount", "field": "amount", "aggFunc": "sum"},
            ],
        ))
        self.assertEqual(resp.status_code, 200, resp.data)

        body = resp.data
        self.assertIn(
            "pivotResultFields", body,
            f"Pivot response must include pivotResultFields; got {body!r}",
        )
        pivot_fields = body.get("pivotResultFields") or []
        # 3 distinct statuses × 1 value col = 3 aliases.
        self.assertEqual(
            len(pivot_fields), 3,
            f"Expected 3 pivot columns; got {pivot_fields!r}",
        )

        rows = body.get("rowData") or []
        self.assertEqual(
            len(rows), 1,
            f"Deepest-level pivot returns one aggregated row; got {rows!r}",
        )
        aggregated = rows[0]

        # Totals per status (from fixture):
        #   active   =  10 + 900 = 910
        #   archived = 250 + 1000 = 1250
        #   draft    =  500
        totals_by_value = {
            Decimal(str(v))
            for v in aggregated.values()
            if isinstance(v, (int, float, str, Decimal)) and str(v).replace(".", "").isdigit()
        }
        self.assertEqual(
            totals_by_value,
            {Decimal("910"), Decimal("1250"), Decimal("500")},
            f"Pivot totals per status must match fixture; "
            f"aggregated row was {aggregated!r}",
        )


class TestCluster14c_EdgeCases(E2ETestCase):
    """Edge cases — unknown fields must be silently dropped."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.cat = QueryCategory.objects.create(name="x")
        QueryItem.objects.create(name="r1", amount=Decimal("5.00"), category=self.cat)
        QueryItem.objects.create(name="r2", amount=Decimal("7.00"), category=self.cat)

    def _post(self, ag_request):
        return self.client.post(
            self.url_list(ITEM),
            data={"request": ag_request},
            format="json",
        )

    # -- 14.19 ---------------------------------------------------------
    def test_14_19_invalid_filter_field_is_ignored(self) -> None:
        """Scenario 14.19: ``filterModel`` naming a field that doesn't
        exist is dropped by ``_is_valid_field_path``; response is the
        unfiltered row set (does NOT crash)."""
        resp = self._post(_ag(
            filterModel={
                "this_field_does_not_exist": {
                    "filterType": "text", "type": "contains", "filter": "x",
                },
            },
        ))
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("rowData") or []
        self.assertEqual(
            len(rows), 2,
            f"Invalid filter field must be ignored; got {rows!r}",
        )

    # -- 14.20 ---------------------------------------------------------
    def test_14_20_invalid_sort_field_falls_back_to_pk(self) -> None:
        """Scenario 14.20: ``sortModel`` with a non-existent ``colId`` is
        dropped by ``_is_valid_field_path``; default PK order applied,
        and the request does not fail."""
        resp = self._post(_ag(
            sortModel=[{"colId": "nonexistent", "sort": "desc"}],
        ))
        self.assertEqual(resp.status_code, 200, resp.data)
        rows = resp.data.get("rowData") or []
        self.assertEqual(len(rows), 2)
        # Must still have deterministic ordering — the default PK order
        # means the first-inserted row (r1) comes before r2.
        names = [r.get("name") for r in rows]
        self.assertEqual(
            names, ["r1", "r2"],
            f"Default PK order must apply when sort field is invalid; "
            f"got {names!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

