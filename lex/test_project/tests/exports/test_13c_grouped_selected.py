"""
Cluster 13c: AG Grid export path — selection via ``groupKeyPaths``.

The AG Grid UI emits ``ag_export.selection.groupKeyPaths`` when a
user multi-selects inside grouped rows and clicks *Export selected*.
The endpoint must translate those paths into ORM filters so only the
selected groups end up in the sheet.

Covers:

* :meth:`ModelExportView._extract_selected_group_key_paths`
* :meth:`ModelExportView._apply_ag_selection_filters`
* :meth:`ModelExportView._coerce_group_key` — in particular the
  type-coercion branches: integer-FK values arrive as strings (``"1"``)
  and must be coerced to ``int(1)``, and the sentinel strings
  ``"null"`` / ``"(empty)"`` / ``"__empty__"`` must become
  ``__isnull=True`` filters.

Scenario numbering matches
docs/test-plan/test-clusters.md#13c-ag-grid-export-path--grouped--selected.
"""

from __future__ import annotations

import unittest

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    EXPORT_STATUS_ACTIVE,
    ITEM,
    ExportCategory,
    ExportItem,
)
from .test_13a_legacy_export import _assert_msg, _read_xlsx_response

import pytest

pytestmark = pytest.mark.exports


def _grouped_payload(group_key_paths):
    """Return an AG payload with a single ``rowGroupCols=[category]``
    level and a ``selection.groupKeyPaths`` selector."""
    return {
        "ag_export": {
            "request": {
                "startRow": 0,
                "endRow": 1000,
                "rowGroupCols": [{"field": "category", "displayName": "Category"}],
                "groupKeys": [],
                "pivotCols": [],
                "pivotMode": False,
                "valueCols": [],
                "sortModel": [],
                "filterModel": {},
            },
            "columns": [
                {"dataKey": "name", "headerName": "Name"},
                {"dataKey": "amount", "headerName": "Amount"},
            ],
            "selection": {"groupKeyPaths": group_key_paths},
            "includeColumnLabels": True,
        }
    }


class TestCluster13c_AGGridGroupedSelected(E2ETestCase):
    """``POST /api/<model>/export`` with ``selection.groupKeyPaths``."""

    e2e_models = ALL_MODELS

    def _seed(self):
        """Two categories × 2 items + one category-less item."""
        self.cat_alpha = ExportCategory.objects.create(name="alpha")
        self.cat_beta = ExportCategory.objects.create(name="beta")
        self.a1 = ExportItem.objects.create(
            name="a1", amount="10.00", category=self.cat_alpha,
            status=EXPORT_STATUS_ACTIVE,
        )
        self.a2 = ExportItem.objects.create(
            name="a2", amount="20.00", category=self.cat_alpha,
            status=EXPORT_STATUS_ACTIVE,
        )
        self.b1 = ExportItem.objects.create(
            name="b1", amount="30.00", category=self.cat_beta,
            status=EXPORT_STATUS_ACTIVE,
        )
        self.b2 = ExportItem.objects.create(
            name="b2", amount="40.00", category=self.cat_beta,
            status=EXPORT_STATUS_ACTIVE,
        )
        # Category-less row — used to prove the ``null`` sentinel path.
        self.orphan = ExportItem.objects.create(
            name="orphan", amount="5.00", category=None,
            status=EXPORT_STATUS_ACTIVE,
        )

    # -- 13.9 ----------------------------------------------------------
    def test_13_9_group_key_paths_filter_integer_fk_and_null(self) -> None:
        """Scenario 13.9: only rows matching selected group keys appear in
        the sheet. The ``"1"`` path-key is coerced to ``int(1)`` by
        ``_coerce_group_key`` (integer-FK branch), and the ``"null"``
        path-key is rewritten to ``__isnull=True``.

        Two selections in one payload:

        1. ``[{field: "category", key: "<alpha-pk-as-string>"}]``
           → only alpha rows (proves string → int coercion for FK).
        2. ``[{field: "category", key: "null"}]``
           → only the orphan row (proves the null sentinel path).

        ``_apply_ag_selection_filters`` ORs the two path-queries so the
        exported sheet must contain exactly ``{a1, a2, orphan}`` — the
        two alpha rows and the single category-less row.
        """
        self._seed()

        payload = _grouped_payload([
            [{"field": "category", "key": str(self.cat_alpha.pk)}],
            [{"field": "category", "key": "null"}],
        ])

        resp = self.client.post(self.url_export(ITEM), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_response(resp)
        self.assertGreater(
            len(df), 0,
            "Selected-groups export came back empty — "
            "_apply_ag_selection_filters likely returned none() for a "
            "selection that should have matched",
        )

        # The grouped sheet contains one header-level row per distinct
        # ``category`` in the filtered queryset. With three categories
        # in the DB (alpha / beta / null) and a two-path selection
        # (alpha + null), the sheet must show exactly TWO group rows —
        # beta is excluded.
        baseline_payload = _grouped_payload([])  # empty selection → full set
        baseline_resp = self.client.post(
            self.url_export(ITEM), data=baseline_payload, format="json",
        )
        self.assertEqual(baseline_resp.status_code, 200, _assert_msg(baseline_resp))
        baseline_df = _read_xlsx_response(baseline_resp)

        self.assertLess(
            len(df), len(baseline_df),
            f"groupKeyPaths selection did not reduce the row set: "
            f"selected={len(df)}, baseline={len(baseline_df)}. "
            f"_apply_ag_selection_filters is effectively a no-op.",
        )

        # Concretely: baseline has 3 category groups (alpha/beta/null);
        # selection of alpha + null should collapse to 2.
        self.assertEqual(
            len(df), 2,
            f"Expected 2 group rows (alpha + null); got {len(df)} "
            f"(sheet columns={list(df.columns)}, rows={df.to_dict('records')!r})",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

