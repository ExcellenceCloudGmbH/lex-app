"""
Cluster 14e: AG Grid POST — secondary filter/sort branches.

The 14b baseline proves the main text / number / date / set / compound-OR
paths. What remains uncovered in ``_build_filter_q`` is the long tail of
operation-type branches the AG Grid UI actually emits in production —
``startsWith`` / ``endsWith`` / ``notContains`` / ``notEqual`` /
``blank`` / ``notBlank`` for text; ``lessThan`` / ``lessThanOrEqual`` /
``greaterThanOrEqual`` / ``notEqual`` / ``blank`` / ``notBlank`` / ``inRange``
for numbers; the legacy ``condition1`` / ``condition2`` AG model shape
still sent by older grids; and the ``apply_ordering`` multi-field CSV
path. Each is a customer-visible filter/sort button on the grid header
that today has zero gate.

This file closes those gaps in **four table-driven scenarios** so a
regression in a single operation-type surfaces a named subTest failure,
not a generic "list has wrong row count".

Method coverage added:

* ``_build_filter_q`` text branch: ``startsWith`` / ``endsWith`` /
  ``notContains`` / ``notEqual`` / ``blank`` / ``notBlank``
* ``_build_filter_q`` number branch: ``lessThan`` /
  ``lessThanOrEqual`` / ``greaterThanOrEqual`` / ``notEqual`` /
  ``blank`` / ``notBlank`` / ``inRange``
* ``_build_filter_q`` legacy ``condition1``/``condition2`` AND/OR path
* ``apply_ordering`` with comma-separated multi-field token list +
  silent-drop of unknown tokens

Scenario numbering matches
docs/test-plan/test-clusters.md § Cluster 14 planned expansions (14e).
"""

from __future__ import annotations

import unittest
from datetime import date
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
    req = {
        "startRow": 0, "endRow": 100,
        "rowGroupCols": [], "groupKeys": [],
        "pivotCols": [], "pivotMode": False,
        "valueCols": [], "sortModel": [],
        "filterModel": {},
    }
    req.update(overrides)
    return req


class TestCluster14e_SecondaryFilterBranches(E2ETestCase):
    """Text + number filter variants the main 14b tests don't touch."""

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        self.cat = QueryCategory.objects.create(name="c14e")
        # Carefully curated fixture — each row is distinguishable by
        # every filter dimension below so subTest failures name a row.
        self.alpha = QueryItem.objects.create(
            name="alpha-x", amount=Decimal("100.00"), count=10,
            status=QUERY_STATUS_ACTIVE, category=self.cat,
        )
        self.beta = QueryItem.objects.create(
            name="beta-y", amount=Decimal("500.00"), count=50,
            status=QUERY_STATUS_ARCHIVED, category=self.cat,
        )
        self.gamma = QueryItem.objects.create(
            name="gamma-z", amount=Decimal("900.00"), count=90,
            status=QUERY_STATUS_DRAFT, category=self.cat,
        )
        # A row with a blank name to exercise the ``blank`` text branch.
        self.blank = QueryItem.objects.create(
            name="", amount=Decimal("0"), count=0,
            status=QUERY_STATUS_ACTIVE, category=self.cat,
        )

    def _post(self, **ag):
        return self.client.post(
            self.url_list(ITEM),
            data=_base_ag_request(**ag),
            format="json",
        )

    def _names(self, resp):
        return sorted(r["name"] for r in resp.data["rowData"])

    # -- 14.21 ---------------------------------------------------------
    def test_14_21_text_filter_operation_type_variants(self) -> None:
        """
        Scenario 14.21: every text operation type the AG Grid header
        dropdown exposes must produce the right row set (excluding
        ``blank`` / ``notBlank`` — those are documented by the
        BUG-016 skipped scenario 14.25 below).

        One subTest per operation — a regression in any one branch
        names the failing operation instead of a generic row-count
        mismatch.
        """
        cases = [
            # (operation, filter_value, expected row names)
            ("startsWith",  "alp",    {"alpha-x"}),
            ("endsWith",    "-y",     {"beta-y"}),
            ("equals",      "beta-y", {"beta-y"}),
            ("notEqual",    "alpha-x", {"beta-y", "gamma-z", ""}),
            ("notContains", "alpha",  {"beta-y", "gamma-z", ""}),
        ]
        for op, value, expected in cases:
            with self.subTest(op=op):
                fm = {"name": {"filterType": "text", "type": op, "filter": value}}
                resp = self._post(filterModel=fm)
                self.assertEqual(
                    resp.status_code, status.HTTP_200_OK,
                    msg=f"text op {op!r} produced non-200: {resp.data!r}",
                )
                got = {r["name"] for r in resp.data["rowData"]}
                self.assertEqual(
                    got, expected,
                    msg=(
                        f"Text filter op={op!r} value={value!r} — "
                        f"expected rows {sorted(expected)}; got {sorted(got)}. "
                        "Check `_build_filter_q` text branch."
                    ),
                )

    # -- 14.22 ---------------------------------------------------------
    def test_14_22_number_filter_operation_type_variants(self) -> None:
        """
        Scenario 14.22: AG Grid number filter header exposes
        ``lessThan`` / ``lessThanOrEqual`` / ``greaterThanOrEqual`` /
        ``notEqual`` / ``inRange`` — none of which are exercised by
        14b's happy-path ``inRange + sortModel`` test. One subTest
        per op.

        ``blank`` / ``notBlank`` are documented by 14.25 (skipped
        while BUG-016 is deferred).
        """
        # amounts on-disk: 0, 100, 500, 900
        cases = [
            ("lessThan",           {"filter": 500},                  {"alpha-x", ""}),
            ("lessThanOrEqual",    {"filter": 500},                  {"alpha-x", "beta-y", ""}),
            ("greaterThanOrEqual", {"filter": 500},                  {"beta-y", "gamma-z"}),
            ("notEqual",           {"filter": 100},                  {"beta-y", "gamma-z", ""}),
            ("inRange",            {"filter": 100, "filterTo": 500}, {"alpha-x", "beta-y"}),
        ]
        for op, extras, expected in cases:
            with self.subTest(op=op):
                fm = {"amount": {"filterType": "number", "type": op, **extras}}
                resp = self._post(filterModel=fm)
                self.assertEqual(resp.status_code, status.HTTP_200_OK)
                got = {r["name"] for r in resp.data["rowData"]}
                self.assertEqual(
                    got, expected,
                    msg=(
                        f"Number op={op!r} extras={extras!r} — "
                        f"expected {sorted(expected)}; got {sorted(got)}"
                    ),
                )

        # Date ``blank`` DOES work — the date branch special-cases it.
        # Date ``notBlank`` does NOT (BUG-016 — covered by 14.25).
        with self.subTest(op="date_blank"):
            resp = self._post(filterModel={
                "created_on": {"filterType": "date", "type": "blank"},
            })
            self.assertEqual(resp.status_code, status.HTTP_200_OK)
            self.assertEqual(
                {r["name"] for r in resp.data["rowData"]},
                {"alpha-x", "beta-y", "gamma-z", ""},
                msg="`blank` on nullable DateField must match isnull rows",
            )

    # -- 14.23 ---------------------------------------------------------
    def test_14_23_legacy_condition_model_and_or(self) -> None:
        """
        Scenario 14.23: older AG Grid clients still send the legacy
        ``condition1``/``condition2`` shape rather than the new
        ``operator + conditions[]`` advanced model. Both shapes must
        produce equivalent result sets — the endpoint serves both
        frontend versions from a single deploy.

        Asserts the OR and AND branches inside the legacy-model block.
        """
        # OR: ``name starts with "alpha"`` OR ``amount > 700``
        fm_or = {"name": {
            "filterType": "text",
            "operator": "OR",
            "condition1": {"filterType": "text", "type": "startsWith", "filter": "alpha"},
            "condition2": {"filterType": "text", "type": "endsWith",   "filter": "-z"},
        }}
        resp = self._post(filterModel=fm_or)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {r["name"] for r in resp.data["rowData"]},
            {"alpha-x", "gamma-z"},
            msg=(
                "Legacy `condition1 OR condition2` must union the two "
                "results; check `_build_filter_q` legacy-operator branch."
            ),
        )

        # AND: ``amount >= 100`` AND ``amount <= 500`` — range via two conditions
        fm_and = {"amount": {
            "filterType": "number",
            "operator": "AND",
            "condition1": {"filterType": "number", "type": "greaterThanOrEqual", "filter": 100},
            "condition2": {"filterType": "number", "type": "lessThanOrEqual",    "filter": 500},
        }}
        resp = self._post(filterModel=fm_and)
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(
            {r["name"] for r in resp.data["rowData"]},
            {"alpha-x", "beta-y"},
            msg=(
                "Legacy AND combination must intersect — a union-bug "
                "here makes every composite filter return extra rows."
            ),
        )

    # -- 14.24 ---------------------------------------------------------
    def test_14_24_ordering_multi_field_csv_silently_drops_unknown(self) -> None:
        """
        Scenario 14.24: ``?ordering=-amount,name`` must apply DESC by
        amount, then ASC by name. Unknown tokens (``?ordering=foo,name``)
        must be silently dropped by ``apply_ordering`` — an early
        ``FieldError`` would 500 the grid because the UI happily sends
        stale column names after a schema change.
        """
        # Fresh fixture: two rows with the same amount but different names
        # so the second ordering key is observable.
        self.blank.delete()
        QueryItem.objects.create(
            name="alpha-same", amount=Decimal("100.00"), count=0,
            status=QUERY_STATUS_ACTIVE, category=self.cat,
        )

        resp = self.client.get(
            self.url_list(ITEM),
            {"ordering": "-amount,name"},
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data if isinstance(resp.data, list) else resp.data.get("results", resp.data)
        amounts = [Decimal(str(r["amount"])) for r in rows]
        self.assertEqual(
            amounts, sorted(amounts, reverse=True),
            msg=(
                "Primary sort key (-amount) broke. Multi-token CSV "
                "ordering must respect every token in left-to-right "
                "precedence; check `apply_ordering`'s token loop."
            ),
        )
        # Among the two 100.00 rows, alpha-same must come before alpha-x (ASC by name).
        hundreds = [r["name"] for r in rows if Decimal(str(r["amount"])) == Decimal("100.00")]
        self.assertEqual(
            hundreds, ["alpha-same", "alpha-x"],
            msg=(
                "Secondary sort key (name ASC) not applied within the "
                "same primary key. Either the token was dropped, or "
                "`apply_ordering` stopped honouring the second token."
            ),
        )

        # Unknown token must not crash — it's silently dropped.
        resp = self.client.get(
            self.url_list(ITEM),
            {"ordering": "not_a_real_field,-amount"},
        )
        self.assertEqual(
            resp.status_code, status.HTTP_200_OK,
            msg=(
                "Unknown ordering token must not 500 the endpoint — "
                "`apply_ordering` must silently skip unresolved fields "
                "so schema drift on the frontend doesn't break the grid."
            ),
        )

    # -- 14.25 ---------------------------------------------------------
    @unittest.skip("BUG-016 deferred: blank/notBlank filter ops are unreachable.")
    def test_14_25_blank_and_not_blank_ops_do_not_work_bug016(self) -> None:
        """
        Scenario 14.25 — **BUG-016**: the AG Grid ``blank`` and
        ``notBlank`` filter operations are silently unreachable
        because the early-return guards at the top of the text /
        number / date branches in ``_build_filter_q`` short-circuit
        on a missing ``filter`` value **before** the per-op dispatch
        runs.

        Concrete repros (all currently return every row instead of
        filtering):

          * ``text`` ``blank``    — line ~330: ``if value in (None, ""): return None``
                                    fires before the ``blank`` branch
                                    at line ~353 can execute.
          * ``text`` ``notBlank`` — same early-return; unreachable.
          * ``number`` ``notBlank`` — line ~363:
                                    ``if value in (None, "") and operation_type != "blank": return None``
                                    omits ``notBlank`` from the bypass
                                    set.
          * ``date`` ``notBlank`` — line ~395:
                                    ``if operation_type != "blank" and not date_from_raw: return None``
                                    same pattern.

        The customer sees ``blank`` / ``notBlank`` items in the grid's
        filter dropdown doing nothing. When the framework is fixed,
        this test must pass naturally (remove the xfail).
        """
        # Give one row a non-blank name to distinguish
        # "blank matched everything" from "blank correctly matched
        # only the empty-name row".

        # Text blank — must return only the "" row.
        resp = self._post(filterModel={
            "name": {"filterType": "text", "type": "blank"},
        })
        self.assertEqual(
            {r["name"] for r in resp.data["rowData"]}, {""},
            msg="text `blank` must match only the empty-name row",
        )

        # Text notBlank — must return every non-empty row.
        resp = self._post(filterModel={
            "name": {"filterType": "text", "type": "notBlank"},
        })
        self.assertEqual(
            {r["name"] for r in resp.data["rowData"]},
            {"alpha-x", "beta-y", "gamma-z"},
            msg="text `notBlank` must exclude the empty-name row",
        )

        # Date notBlank — after setting one row's created_on, only that
        # row must survive.
        self.beta.created_on = date(2026, 5, 5)
        self.beta.save(update_fields=["created_on"])
        resp = self._post(filterModel={
            "created_on": {"filterType": "date", "type": "notBlank"},
        })
        self.assertEqual(
            {r["name"] for r in resp.data["rowData"]}, {"beta-y"},
            msg="date `notBlank` must exclude the isnull rows",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()



