"""
Cluster 2g: List filtering via arbitrary query params.

Intent (from List.py's ``apply_query_param_filters`` + RESERVED_QUERY_PARAMS):

    Any query parameter whose name is NOT reserved for pagination /
    ordering / view control is interpreted as a field filter.
    Supported suffixes: ``__exact`` (default), ``__icontains``,
    ``__gt``/``__gte``/``__lt``/``__lte``, ``__in``, ``__range``,
    ``__isnull``, ``__startswith``, etc. A trailing ``!`` on the
    param name negates the filter.

    Unknown fields are silently dropped — a stale frontend never
    crashes the backend.

Scenario numbering extends
docs/test-plan/test-clusters.md#2-crud-via-rest-api (2.30–2.36).
"""

from __future__ import annotations

import unittest

from rest_framework import status

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, SIMPLE, SimpleItem


class TestCluster02g_Filtering(E2ETestCase):
    """Query-param filtering contract on the list endpoint."""

    e2e_models = ALL_MODELS

    def _names(self, resp) -> set[str]:
        return {r["name"] for r in self.extract_results(resp.data)}

    # -- 2.30 ----------------------------------------------------------
    def test_2_30_filter_exact_match(self) -> None:
        """Scenario 2.30: ``?name=alpha`` returns only matching rows."""
        SimpleItem.objects.create(name="alpha", value=1)
        SimpleItem.objects.create(name="bravo", value=2)

        resp = self.client.get(self.url_list(SIMPLE), data={"name": "alpha"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha"},
            "Exact-match filter must return only the matching name",
        )

    # -- 2.31 ----------------------------------------------------------
    def test_2_31_filter_lookup_gt(self) -> None:
        """Scenario 2.31: ``?value__gt=5`` returns rows with value > 5."""
        for v in (1, 5, 6, 10):
            SimpleItem.objects.create(name=f"g-{v}", value=v)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"value__gt": 5},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = {r["value"] for r in self.extract_results(resp.data)}
        self.assertEqual(
            values, {6, 10},
            f"``value__gt=5`` must yield {{6, 10}}; got {values!r}",
        )

    # -- 2.32 ----------------------------------------------------------
    def test_2_32_filter_lookup_icontains(self) -> None:
        """Scenario 2.32: ``?name__icontains=al`` returns case-insensitive substring matches."""
        SimpleItem.objects.create(name="Alpha", value=1)
        SimpleItem.objects.create(name="bravo", value=2)
        SimpleItem.objects.create(name="Calorie", value=3)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"name__icontains": "al"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"Alpha", "Calorie"},
            "icontains must be case-insensitive substring match",
        )

    # -- 2.33 ----------------------------------------------------------
    def test_2_33_filter_combined(self) -> None:
        """Scenario 2.33: Two filters combine as AND."""
        SimpleItem.objects.create(name="alpha", value=1)
        SimpleItem.objects.create(name="alpha", value=9)
        SimpleItem.objects.create(name="bravo", value=9)

        resp = self.client.get(
            self.url_list(SIMPLE),
            data={"name": "alpha", "value__gte": 5},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        self.assertEqual(len(rows), 1, f"Only one row matches both filters; got {rows}")
        self.assertEqual(rows[0]["name"], "alpha")
        self.assertEqual(rows[0]["value"], 9)

    # -- 2.34 ----------------------------------------------------------
    def test_2_34_filter_empty_result(self) -> None:
        """Scenario 2.34: No match → 200 with empty results, not 404."""
        SimpleItem.objects.create(name="alpha", value=1)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"name": "nonesuch"},
        )

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            "Empty filter result must be 200 with empty results, not 404",
        )
        rows = self.extract_results(resp.data)
        self.assertEqual(len(rows), 0, f"Expected empty results; got {rows}")

    # -- 2.35 ----------------------------------------------------------
    def test_2_35_filter_negation(self) -> None:
        """Scenario 2.35: ``?name!=alpha`` excludes matching rows."""
        SimpleItem.objects.create(name="alpha", value=1)
        SimpleItem.objects.create(name="bravo", value=2)
        SimpleItem.objects.create(name="charlie", value=3)

        resp = self.client.get(self.url_list(SIMPLE), data={"name!": "alpha"})

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"bravo", "charlie"},
            "``key!=value`` must exclude the matching rows",
        )

    # -- 2.36 ----------------------------------------------------------
    def test_2_36_filter_in_lookup(self) -> None:
        """Scenario 2.36: ``?name__in=a,b`` returns rows whose name is in the set."""
        SimpleItem.objects.create(name="alpha", value=1)
        SimpleItem.objects.create(name="bravo", value=2)
        SimpleItem.objects.create(name="charlie", value=3)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"name__in": "alpha,bravo"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha", "bravo"},
            "``__in=a,b`` must match the set of values",
        )

    # -- 2.37 ----------------------------------------------------------
    def test_2_37_unknown_filter_field_is_ignored(self) -> None:
        """
        Scenario 2.37: Unknown filter field must not 500.

        ``_resolve_lookup`` returns ``None`` for unknown fields and
        ``apply_query_param_filters`` silently drops those — a stale
        frontend never crashes the backend.
        """
        SimpleItem.objects.create(name="alpha", value=1)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"no_such_field": "x"},
        )

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Unknown filter field must be silently ignored; got {resp.status_code}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()




