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

14.73 (C1 final-review fix) closes a second gap in the same GET grammar:
``_coerce_value`` ran for every lookup, bare key or not, and -- before any
field-type logic -- lowered any value spelling ``null``/``none`` to Python
``None`` and any ``true``/``false`` to a bool, for EVERY field type. A
value-list Set filter's "Select All" / group-selection fetch sends
``field__exact=<key>`` for one ticked key and repeated ``field__in=<key>``
for several (the same GET grammar 14.71/14.72 cover), so a ``CharField``
choice column whose stored key is the literal string ``"none"`` produced
``field__exact=none`` -- silently rewritten to ``IS NULL`` -- selecting the
blank rows instead of the ``"none"`` row the grid shows; inside ``__in`` the
same key converted to ``None`` and was dropped. ``__exact`` and ``__in`` now
convert each value the same way the POST Set filter already does
(``_set_filter_values``): the target field's ``to_python`` for a foreign
key, ``_bool_from_key`` for a boolean column, and the field's own
``to_python`` otherwise -- via a conversion helper shared with
``_set_filter_values`` (``_typed_value_converter`` / ``_coerce_typed_value``)
so both paths treat a stored value identically. A value that fails this
conversion is dropped (and the whole param ignored if none survive) rather
than ever becoming a 400 -- unlike the POST filterModel path, a GET list
request stays a read. Bare keys (``?field=value``, no suffix) and every
other lookup suffix are untouched: they still run through the legacy,
best-effort ``_coerce_value``, so an existing caller's ``?field=null``
keeps meaning ``IS NULL``.

Cluster 14j — scenarios 14.71-14.73. Type: E.
Covers: lex/api/views/model_entries/List.py (SAFE_LOOKUPS, _resolve_lookup,
_build_query_from_values, _typed_value_converter, _coerce_typed_value,
_set_filter_values).
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


class TestCluster14j_ExactAndInTypeConversion(E2ETestCase):
    """``GET .../list?<field>__exact=`` / ``__in=`` convert each value by the
    column's own type -- the same conversion the POST Set filter already
    uses -- instead of the legacy, type-blind ``_coerce_value``."""

    e2e_models = ALL_MODELS

    def _seed(self):
        # `note`: a nullable plain CharField with no choices ("a choice or
        # char column" -- `status` is a real choice field but is NOT
        # nullable, so it cannot carry the required NULL row). `epsilon` and
        # `zeta` store the literal strings "none" and "true" -- exactly the
        # spellings `_coerce_value` singles out for every field type -- and
        # `eta` is the actual NULL row they must never be confused with.
        # `is_active`: the real BooleanField, to prove the shared converter
        # still produces real booleans for one. `category`: a direct FK, to
        # prove the shared converter's target-field branch still produces
        # ids for one. `theta` is an uninvolved decoy row (non-null note,
        # no category) so a wrongly-unfiltered param would be caught by an
        # extra name in the result, not hidden by an all-rows vacuous pass.
        self.cat_one = QueryCategory.objects.create(name="cat-one")
        self.cat_two = QueryCategory.objects.create(name="cat-two")
        self.epsilon = QueryItem.objects.create(
            name="epsilon", note="none", is_active=True, category=self.cat_one,
        )
        self.zeta = QueryItem.objects.create(
            name="zeta", note="true", is_active=False, category=self.cat_two,
        )
        self.eta = QueryItem.objects.create(
            name="eta", note=None, is_active=True, category=None,
        )
        self.theta = QueryItem.objects.create(
            name="theta", note="other", is_active=False, category=None,
        )

    def _names(self, resp) -> set[str]:
        """Extract the ``name`` set from a list response, paginated or flat."""
        rows = self.extract_results(resp.data)
        return {r["name"] for r in rows if isinstance(r, dict) and "name" in r}

    # -- 14.73 -------------------------------------------------------------
    def test_14_73_exact_and_in_convert_by_field_type_not_legacy_coercion(self) -> None:
        """
        Scenario 14.73: ``?col__exact=`` / ``?col__in=`` convert by the
        model field's type (C1, final review).
        Given: a ``QueryItem`` row whose ``note`` literally stores ``"none"``,
            one whose ``note`` literally stores ``"true"``, one whose
            ``note`` is actually NULL, and a decoy row; the same rows carry
            an ``is_active`` boolean and (for two of them) a ``category`` FK.
        When: the list GET endpoint is filtered through ``__exact`` and
            ``__in`` on the text, boolean and foreign-key columns, and
            through a bare key with no suffix.
        Then: ``__exact``/``__in`` convert each value by the column's type
            (never confusing a literal "none"/"true" string with NULL or a
            bool), while the bare, suffix-less key keeps meaning NULL for
            ``"null"`` -- the legacy behaviour existing callers depend on.
        """
        self._seed()

        # 14.73(a): CharField __exact -- the literal string "none" survives
        # the field's own to_python unchanged, so it matches ONLY the row
        # that actually stores it, never the NULL row (`eta`). Before the
        # fix `_coerce_value` lowered "none" to Python `None` here, which
        # `Q(note__exact=None)` renders as `note IS NULL`.
        resp = self.list_get(ITEM, {"note__exact": "none"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"epsilon"},
            f"note__exact=none must match the literal \"none\" row only, "
            f"not the NULL row; got {self._names(resp)!r}",
        )

        # 14.73(b): CharField __in -- both literal keys convert (unchanged
        # strings) and match; the NULL row and the decoy are excluded. Before
        # the fix "none"/"true" became None/True and dropped out of the IN
        # list (or matched the wrong rows).
        resp = self.list_get(ITEM, {"note__in": ["none", "true"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"epsilon", "zeta"},
            f"note__in=none,true must match both literal rows only; "
            f"got {self._names(resp)!r}",
        )

        # 14.73(c): BooleanField __exact -- `_bool_from_key` still converts
        # "true" to the real boolean True (already correct before this fix;
        # a regression guard on the now-shared conversion helper).
        resp = self.list_get(ITEM, {"is_active__exact": "true"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"epsilon", "eta"},
            f"is_active__exact=true must match both True rows; "
            f"got {self._names(resp)!r}",
        )

        # 14.73(d): BooleanField __in with both keys -- still converts and
        # matches every row regardless of its is_active value.
        resp = self.list_get(ITEM, {"is_active__in": ["true", "false"]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"epsilon", "zeta", "eta", "theta"},
            f"is_active__in=true,false must match every row; "
            f"got {self._names(resp)!r}",
        )

        # 14.73(e): ForeignKey __in with repeated params -- the target
        # field's to_python still converts each id; rows with no category
        # are excluded.
        resp = self.list_get(ITEM, {"category__in": [self.cat_one.pk, self.cat_two.pk]})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"epsilon", "zeta"},
            f"category__in=<id>,<id> must match both FK rows; "
            f"got {self._names(resp)!r}",
        )

        # 14.73(f): a bare key (no lookup suffix) keeps the legacy,
        # type-blind _coerce_value -- "null" still means IS NULL, exactly
        # as an existing API caller relies on, and still does NOT match the
        # rows literally storing "none" or "true".
        resp = self.list_get(ITEM, {"note": "null"})
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            self._names(resp), {"eta"},
            f"bare note=null must still mean IS NULL (legacy coercion); "
            f"got {self._names(resp)!r}",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
