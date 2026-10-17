"""
Cluster 14j: GET list — the ``__blank`` query-param lookup suffix.

Intent: "Select All Records" fetches ids through the list GET endpoint's
query-param filters (the ``pk_only=true`` shortcut pinned by 14.7), not the
POST AG Grid endpoint. Its notion of "blank" must match what
``_build_filter_q`` (the POST filter translator) already calls blank,
pinned for POST by 14.37/14.61 and documented in
docs/superpowers/specs/2026-10-14-multi-value-filters-and-cross-filtering-design.md:
on a plain text column (a ``CharField``/``TextField`` with no ``choices`` --
``is_plain_text_field``) blank means NULL **or** ``""``; on every other
column blank means NULL only. Before this change the GET grammar could only
ask ``field__isnull=true``, so a GET-driven "Select All" undercounted the
``""`` rows the POST path (and the grid itself) already treats as blank.

This file adds the ``__blank`` GET lookup suffix. ``blank`` is not a real
Django lookup: ``_resolve_lookup`` now accepts it as a safe non-Django
suffix (alongside ``SAFE_LOOKUPS``), and ``_build_query_from_values``
builds the ``Q`` itself instead of handing Django a ``field__blank``
lookup it would reject.

Cluster 14j — scenarios 14.71-14.72. Type: E.
Covers: lex/api/views/model_entries/List.py (SAFE_LOOKUPS, _resolve_lookup,
_build_query_from_values).
Run: python -m lex pytest lex/test_project/tests/queries/test_14j_blank_query_param.py -v
"""

from __future__ import annotations

import unittest
from datetime import date

from lex.test_project.tests._e2e_test_case import E2ETestCase
from rest_framework import status

from .models import ALL_MODELS, ITEM, QueryCategory, QueryItem

import pytest

pytestmark = pytest.mark.queries


class TestCluster14j_BlankQueryParam(E2ETestCase):
    """``GET /api/model_entries/queryitem/list?<field>__blank=...``."""

    e2e_models = ALL_MODELS

    def _seed(self):
        # `note`: a plain text column (CharField, no choices) -- blank
        # means NULL or "". `created_on`: a non-text column (DateField) --
        # blank means NULL only (there is no "" state for a DateField).
        # `category`: a direct FK column (its own `__blank` stays
        # supported); only `alpha` has one set, so a wrongly-applied
        # `category__name__blank` filter (treating the related path as
        # supported) would exclude it -- proving the guard, not just
        # failing to crash.
        self.cat = QueryCategory.objects.create(name="cat-alpha")
        self.alpha = QueryItem.objects.create(
            name="alpha", note="filled", created_on=date(2026, 1, 1),
            category=self.cat,
        )
        self.beta = QueryItem.objects.create(
            name="beta", note=None, created_on=date(2026, 2, 1),
        )
        self.gamma = QueryItem.objects.create(
            name="gamma", note="", created_on=date(2026, 3, 1),
        )
        self.delta = QueryItem.objects.create(
            name="delta", note="also-filled", created_on=None,
        )

    def _names(self, resp) -> set[str]:
        """Extract the ``name`` set from a list response, paginated or flat."""
        rows = self.extract_results(resp.data)
        return {r["name"] for r in rows if isinstance(r, dict) and "name" in r}

    # -- 14.71 -------------------------------------------------------------
    def test_14_71_blank_true_matches_null_and_empty_string_on_text_column(self) -> None:
        """
        Scenario 14.71: ``?note__blank=true`` on a plain text column
        (``note``, a ``CharField`` with no ``choices``) matches BOTH the
        NULL row and the ``""`` row -- the same "blank" the POST Set
        filter's blank key already matches (14.37/14.61). On a non-text
        column (``created_on``, a ``DateField``) ``?created_on__blank=true``
        matches only the NULL row -- there is no empty-string state to fold
        in there.
        """
        self._seed()

        resp = self.list_get(ITEM, {"note__blank": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"beta", "gamma"},
            "note__blank=true on a plain text column must match both the "
            f"NULL row and the \"\" row; got {self._names(resp)!r}",
        )

        resp = self.list_get(ITEM, {"created_on__blank": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"delta"},
            "created_on__blank=true on a non-text column must match only "
            f"the NULL row; got {self._names(resp)!r}",
        )

    # -- 14.72 -------------------------------------------------------------
    def test_14_72_blank_false_and_negation_return_non_blank_rows(self) -> None:
        """
        Scenario 14.72: ``?note__blank=false`` and the equivalent negated
        form ``?note__blank!=true`` (the trailing ``!`` ``apply_query_param_
        filters`` already routes through ``.exclude()``) both return
        exactly the rows ``blank=true`` leaves out -- the non-blank rows --
        on both a plain text column and a non-text column.

        14.72(a): ``__blank`` is restricted to a direct column.
        ``?category__name__blank=true`` names a related path (``category``
        is a FK, ``name`` is a column on the *related* model, not this
        one) -- it must be ignored exactly like an unsupported suffix, not
        silently treated as if it were ``category``'s own blank state.

        14.72(b): ``?metadata__key__blank=true`` names a JSON sub-path
        (``metadata`` is a ``JSONField``) -- also ignored, and never a 500
        from Django rejecting a literal ``field__blank`` lookup. This is a
        regression guard: the generic ``Q(**{lookup_path: value})`` branch
        would otherwise receive ``metadata__key__blank`` verbatim.
        """
        self._seed()

        resp = self.list_get(ITEM, {"note__blank": "false"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha", "delta"},
            f"note__blank=false must return the non-blank rows; got {self._names(resp)!r}",
        )

        resp = self.list_get(ITEM, {"note__blank!": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha", "delta"},
            f"note__blank!=true must return the non-blank rows; got {self._names(resp)!r}",
        )

        resp = self.list_get(ITEM, {"created_on__blank": "false"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha", "beta", "gamma"},
            f"created_on__blank=false must return the non-blank rows; got {self._names(resp)!r}",
        )

        resp = self.list_get(ITEM, {"created_on__blank!": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"alpha", "beta", "gamma"},
            f"created_on__blank!=true must return the non-blank rows; got {self._names(resp)!r}",
        )

        all_names = {"alpha", "beta", "gamma", "delta"}

        # -- 14.72(a): a related path's own `__blank` is ignored -- only a
        # direct column (e.g. `category__blank` itself) supports `__blank`.
        resp = self.list_get(ITEM, {"category__name__blank": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), all_names,
            "category__name__blank is a related path, not a direct column, "
            f"and must be ignored (every seeded row returned); got {self._names(resp)!r}",
        )

        # -- 14.72(b): a JSON sub-path's `__blank` is ignored the same way
        # -- regression guard: never a 500 from an unresolvable Django lookup.
        resp = self.list_get(ITEM, {"metadata__key__blank": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), all_names,
            "metadata__key__blank is a JSON sub-path and must be ignored, "
            f"never a 500; got {self._names(resp)!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
