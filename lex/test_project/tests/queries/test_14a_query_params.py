"""
Cluster 14a: GET list — query-param filtering & ordering.

Every scenario drives ``GET /api/model_entries/<model>/list?...`` and
asserts on the **response body** — the real ``apply_query_param_filters``
pipeline runs (``_resolve_lookup`` → ``_coerce_value`` →
``_build_query_from_values``), plus ``apply_ordering`` and the
``CustomPageNumberPagination`` variants.

Method coverage of this file:

* ``apply_query_param_filters``
* ``_resolve_lookup`` / ``_split_safe_lookup_suffix``
* ``_coerce_value`` (int / Decimal / bool / str branches)
* ``_build_query_from_values`` (single / multi / ``__in`` / ``__range``)
* ``apply_ordering`` + ``normalize_field_path``
* ``CustomPageNumberPagination.paginate_queryset`` (``perPage=-1``)
* ``ListModelEntries.list`` (``pk_only=true`` shortcut)

Scenario numbering matches
docs/test-plan/test-clusters.md#14-ag-grid-query-endpoint.
"""

from __future__ import annotations

import unittest
from datetime import date
from decimal import Decimal

from lex.test_project.tests._e2e_test_case import E2ETestCase
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


class TestCluster14a_QueryParamFilters(E2ETestCase):
    """``GET /api/model_entries/queryitem/list?...`` — query-param filtering."""

    e2e_models = ALL_MODELS

    def _seed(self):
        self.cat_alpha = QueryCategory.objects.create(name="alpha")
        self.cat_beta = QueryCategory.objects.create(name="beta")
        self.rows = {
            "alpha-small": QueryItem.objects.create(
                name="alpha-small", amount=Decimal("10.00"), count=1,
                status=QUERY_STATUS_ACTIVE, created_on=date(2026, 1, 15),
                category=self.cat_alpha,
            ),
            "alpha-mid": QueryItem.objects.create(
                name="alpha-mid", amount=Decimal("250.00"), count=5,
                status=QUERY_STATUS_ARCHIVED, created_on=date(2026, 3, 10),
                category=self.cat_alpha,
            ),
            "alpha-big": QueryItem.objects.create(
                name="alpha-big", amount=Decimal("900.00"), count=10,
                status=QUERY_STATUS_ACTIVE, created_on=date(2026, 6, 1),
                category=self.cat_alpha,
            ),
            "beta-mid": QueryItem.objects.create(
                name="beta-mid", amount=Decimal("500.00"), count=0,
                status=QUERY_STATUS_DRAFT, created_on=date(2026, 2, 20),
                category=self.cat_beta,
            ),
            "skip-me": QueryItem.objects.create(
                name="skip-me", amount=Decimal("1000.00"), count=99,
                status=QUERY_STATUS_ARCHIVED, created_on=date(2026, 4, 1),
                category=self.cat_beta,
            ),
        }

    def _names(self, resp) -> set[str]:
        """Extract the ``name`` set from a list response, paginated or flat."""
        rows = self.extract_results(resp.data)
        return {r["name"] for r in rows if isinstance(r, dict) and "name" in r}

    # -- 14.1 ----------------------------------------------------------
    def test_14_1_icontains_substring_match(self) -> None:
        """Scenario 14.1: ``?name__icontains=alp`` → only "alpha-*" rows."""
        self._seed()
        resp = self.list_get(ITEM, {"name__icontains": "alp"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp),
            {"alpha-small", "alpha-mid", "alpha-big"},
            f"icontains must return substring matches only; got {self._names(resp)!r}",
        )

    # -- 14.2 ----------------------------------------------------------
    def test_14_2_decimal_range_filter(self) -> None:
        """Scenario 14.2: ``?amount__gte=100&amount__lte=500`` — Decimal range.

        ``_coerce_value`` must convert the string query-params to
        ``Decimal`` so the SQL comparison is numerically correct (not
        lexicographic).
        """
        self._seed()
        resp = self.list_get(ITEM, {"amount__gte": "100", "amount__lte": "500"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha-mid", "beta-mid"},
            f"Range filter failed; got {self._names(resp)!r}",
        )

    # -- 14.3 ----------------------------------------------------------
    def test_14_3_in_lookup_comma_split(self) -> None:
        """Scenario 14.3: ``?status__in=active,archived`` — single
        comma-separated string must be split inside
        ``_build_query_from_values``.
        """
        self._seed()
        resp = self.list_get(ITEM, {"status__in": "active,archived"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        # Draft row excluded.
        self.assertEqual(
            self._names(resp),
            {"alpha-small", "alpha-mid", "alpha-big", "skip-me"},
            f"__in filter failed; got {self._names(resp)!r}",
        )

    # -- 14.4 ----------------------------------------------------------
    def test_14_4_negated_filter(self) -> None:
        """Scenario 14.4: ``?name!=skip-me`` — trailing ``!`` routes
        through ``.exclude()``."""
        self._seed()
        resp = self.list_get(ITEM, {"name!": "skip-me"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = self._names(resp)
        self.assertNotIn(
            "skip-me", names,
            f"Negated filter must exclude the matched row; got {names!r}",
        )
        self.assertEqual(
            len(names), 4,
            f"Expected 4 rows remaining after excluding one; got {len(names)}",
        )

    # -- 14.5 ----------------------------------------------------------
    def test_14_5_ordering_descending(self) -> None:
        """Scenario 14.5: ``?ordering=-amount`` → rows in DESC amount order."""
        self._seed()
        resp = self.list_get(ITEM, {"ordering": "-amount"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        amounts = [Decimal(str(r["amount"])) for r in rows]
        self.assertEqual(
            amounts, sorted(amounts, reverse=True),
            f"Rows must be sorted DESC by amount; got {amounts!r}",
        )

    # -- 14.6 ----------------------------------------------------------
    def test_14_6_per_page_minus_one_returns_all(self) -> None:
        """Scenario 14.6: ``?perPage=-1`` — pagination envelope still
        present but ``results`` contains every filtered row."""
        self._seed()
        resp = self.list_get(ITEM, {"perPage": "-1"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertEqual(
            len(rows), 5,
            f"perPage=-1 must return every filtered row; got {len(rows)}",
        )

    # -- 14.7 ----------------------------------------------------------
    def test_14_7_pk_only_returns_id_shortcut(self) -> None:
        """Scenario 14.7: ``?pk_only=true&status=active`` — response is
        ``{"ids": [...], "count": N}``, not a serialized row list."""
        self._seed()
        resp = self.list_get(ITEM, {"pk_only": "true", "status": QUERY_STATUS_ACTIVE})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)

        body = resp.data
        self.assertIn("ids", body, f"pk_only response missing 'ids'; got {body!r}")
        self.assertIn("count", body, f"pk_only response missing 'count'; got {body!r}")
        # Two rows are 'active' — alpha-small + alpha-big.
        expected = {
            self.rows["alpha-small"].pk,
            self.rows["alpha-big"].pk,
        }
        self.assertEqual(
            set(body["ids"]), expected,
            f"pk_only must honour filter predicate; got ids={body['ids']!r}",
        )
        self.assertEqual(body["count"], 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

