"""
Cluster 2f: List ordering via ``?ordering=`` query param.

Intent (from docs/features/api-layer/ and List.py's ``apply_ordering``):

    The list endpoint honours ``?ordering=<field>`` to sort ascending
    and ``?ordering=-<field>`` to sort descending. Multiple tokens
    may be comma-separated: ``?ordering=name,-value``. Unknown tokens
    are silently ignored (not 500) so a stale frontend never crashes
    the backend.

Scenario numbering extends
docs/test-plan/test-clusters.md#2-crud-via-rest-api (2.26–2.29).
"""

from __future__ import annotations

import unittest

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, SIMPLE, SimpleItem

import pytest

pytestmark = pytest.mark.crud_api


class TestCluster02f_Ordering(E2ETestCase):
    """``?ordering=`` contract."""

    e2e_models = ALL_MODELS

    def _values(self, resp, key: str) -> list:
        """Extract a single field from the list response (paginated or not)."""
        return [row[key] for row in self.extract_results(resp.data)]

    # -- 2.26 ----------------------------------------------------------
    def test_2_26_ordering_ascending_by_value(self) -> None:
        """Scenario 2.26: ``?ordering=value`` sorts ascending."""
        for v in (3, 1, 2):
            SimpleItem.objects.create(name=f"o-{v}", value=v)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"ordering": "value"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = self._values(resp, "value")
        self.assertEqual(
            values, sorted(values),
            f"Ascending ordering must produce a non-decreasing sequence; "
            f"got {values!r}",
        )

    # -- 2.27 ----------------------------------------------------------
    def test_2_27_ordering_descending_by_value(self) -> None:
        """Scenario 2.27: ``?ordering=-value`` sorts descending."""
        for v in (2, 5, 1, 4):
            SimpleItem.objects.create(name=f"d-{v}", value=v)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"ordering": "-value"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        values = self._values(resp, "value")
        self.assertEqual(
            values, sorted(values, reverse=True),
            f"Descending ordering must produce a non-increasing sequence; "
            f"got {values!r}",
        )

    # -- 2.28 ----------------------------------------------------------
    def test_2_28_ordering_multi_field(self) -> None:
        """
        Scenario 2.28: ``?ordering=name,-value`` sorts by name ASC,
        then value DESC within a name.
        """
        SimpleItem.objects.create(name="alpha", value=1)
        SimpleItem.objects.create(name="alpha", value=5)
        SimpleItem.objects.create(name="beta", value=2)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"ordering": "name,-value"},
        )

        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = self.extract_results(resp.data)
        pairs = [(r["name"], r["value"]) for r in rows]
        # Only the two alpha rows need the tiebreak to be visible.
        alphas = [v for n, v in pairs if n == "alpha"]
        self.assertEqual(
            alphas, [5, 1],
            f"Within name='alpha', values must be DESC per ``-value``; "
            f"got {alphas!r}",
        )

    # -- 2.29 ----------------------------------------------------------
    def test_2_29_ordering_unknown_field_is_ignored(self) -> None:
        """
        Scenario 2.29: Unknown ordering token must not 500.

        ``apply_ordering`` in List.py silently drops tokens that don't
        resolve to a real field — a stale frontend asking for a
        removed column must not take the backend down.
        """
        SimpleItem.objects.create(name="x", value=1)

        resp = self.client.get(
            self.url_list(SIMPLE), data={"ordering": "no_such_field"},
        )

        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            f"Unknown ordering token must yield 200 (token ignored); "
            f"got {resp.status_code}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



