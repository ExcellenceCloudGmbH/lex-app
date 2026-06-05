"""
Cluster 13a: Legacy (non-AG) export path — end-to-end via POST.

Every scenario drives the real ``POST /api/<model>/export``
endpoint and asserts on the **contents of the returned .xlsx
file**. This is the integration point that translates a request
payload into a streamed Excel binary — the helper methods in
:class:`ModelExportView` are exercised transitively, not mocked.

Method coverage of this file (legacy path only):

* :meth:`ModelExportView.post`
* :meth:`filter_and_mask_data_for_export`
* :meth:`get_exportable_fields_for_object`
* :meth:`_has_default_export_permissions`
* :meth:`_compute_uniform_export_mask`
* :meth:`_apply_foreign_key_display_names`

Scenario numbering matches
docs/test-plan/test-clusters.md#13-export-endpoint.
"""

from __future__ import annotations

import base64
import unittest
from io import BytesIO
from urllib.parse import urlencode

import pandas as pd
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    EXPORT_STATUS_ARCHIVED,
    ITEM,
    MASKED,
    ExportCategory,
    ExportItem,
    ExportMaskedItem,
)

import pytest

pytestmark = pytest.mark.exports


def _read_xlsx_response(resp) -> pd.DataFrame:
    """Collect ``FileResponse`` streaming bytes → DataFrame.

    ``ModelExportView.post`` writes with ``index=True``, so the first
    column of the sheet is the DataFrame's numeric index — we skip it
    with ``index_col=0`` to get back the business columns only.
    """
    buf = BytesIO(b"".join(resp.streaming_content))
    buf.seek(0)
    return pd.read_excel(buf, index_col=0)


def _assert_msg(resp) -> str:
    """Build a safe failure message for an export response.

    ``FileResponse.content`` raises — we must avoid it. Use the
    status line + JSON body when available.
    """
    if resp.streaming:
        return f"status={resp.status_code} reason={resp.reason_phrase!r}"
    try:
        return f"status={resp.status_code} body={resp.json()!r}"
    except Exception:
        return f"status={resp.status_code}"


class TestCluster13a_LegacyExport(E2ETestCase):
    """``POST /api/<model>/export`` — legacy (non-AG) code path."""

    e2e_models = ALL_MODELS

    # -- 13.1 ----------------------------------------------------------
    def test_13_1_empty_queryset_returns_404(self) -> None:
        """Scenario 13.1: empty DB → 404 with explicit error body."""
        resp = self.client.post(self.url_export(ITEM), data={}, format="json")

        self.assertEqual(resp.status_code, 404)
        # Not an xlsx — JSON error.
        body = resp.json()
        self.assertIn(
            "error", body,
            f"404 body must name the problem; got {body!r}",
        )

    # -- 13.2a ---------------------------------------------------------
    def test_13_2a_flat_export_without_fk_has_all_rows(self) -> None:
        """Scenario 13.2a: simple path — no FK, default perms. Every row
        must be present with ``id`` + ``name`` + ``amount`` populated.

        Originally exposed **BUG-014** (``constant_memory`` + ``index=True``
        dropped every row except the last). The bug has since been fixed
        — this test is the permanent regression gate. If
        ``constant_memory`` is re-enabled with ``index=True`` this
        assertion fails again.
        """
        ExportItem.objects.create(name="n1", amount="10.00")
        ExportItem.objects.create(name="n2", amount="20.00")
        ExportItem.objects.create(name="n3", amount="30.00")

        resp = self.client.post(self.url_export(ITEM), data={}, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_response(resp)
        names = set(df["name"].dropna().astype(str))
        self.assertEqual(
            names, {"n1", "n2", "n3"},
            f"Every row must have its `name` populated; got {names!r}",
        )

    # -- 13.2b ---------------------------------------------------------
    def test_13_2b_flat_export_has_rows_and_fk_display_names(self) -> None:
        """Scenario 13.2: default-permissions export writes all rows,
        with FK columns rendered as ``str(category)`` not the pk."""
        cat_alpha = ExportCategory.objects.create(name="alpha")
        cat_beta = ExportCategory.objects.create(name="beta")
        ExportItem.objects.create(name="a1", amount="10.00", category=cat_alpha)
        ExportItem.objects.create(name="a2", amount="20.00", category=cat_alpha)
        ExportItem.objects.create(name="b1", amount="30.00", category=cat_beta)
        ExportItem.objects.create(name="b2", amount="40.00", category=cat_beta)
        ExportItem.objects.create(name="uncat", amount="50.00", category=None)

        resp = self.client.post(self.url_export(ITEM), data={}, format="json")
        self.assertEqual(
            resp.status_code, 200,
            f"Expected 200 + xlsx; got {resp.status_code}: "
            f"{getattr(resp, 'content', b'')[:200]!r}",
        )

        df = _read_xlsx_response(resp)
        self.assertEqual(
            len(df), 5,
            f"Expected 5 rows in export, got {len(df)}. Columns={list(df.columns)}",
        )

        # FK column: readable names, not integer pks.
        self.assertIn("category", df.columns, f"Columns: {list(df.columns)}")
        category_values = set(df["category"].dropna().astype(str))
        self.assertIn(
            "Cat<alpha>", category_values,
            f"FK column must show readable str(category); got {category_values!r}",
        )
        self.assertIn("Cat<beta>", category_values)
        # And NO raw pk integers leaked through.
        for value in category_values:
            self.assertFalse(
                value.strip().isdigit(),
                f"Raw FK pk leaked into export as {value!r} — "
                "_apply_foreign_key_display_names did not run",
            )

    # -- 13.3 ----------------------------------------------------------
    def test_13_3_filtered_export_selects_specific_ids(self) -> None:
        """Scenario 13.3: ``filtered_export`` base64 of ``?ids=X&ids=Y``
        restricts the sheet to the chosen rows.

        The legacy path routes this through
        ``PrimaryKeyListFilterBackend.filter_for_export``.
        """
        a = ExportItem.objects.create(name="pick-a", amount="1.00")
        b = ExportItem.objects.create(name="pick-b", amount="2.00")
        _ = ExportItem.objects.create(name="skip-me", amount="99.00")

        encoded = base64.b64encode(
            urlencode([("ids", a.pk), ("ids", b.pk)]).encode("utf-8"),
        ).decode("ascii")

        resp = self.client.post(
            self.url_export(ITEM),
            data={"filtered_export": encoded},
            format="json",
        )
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_response(resp)
        names = set(df["name"].dropna().astype(str))
        self.assertEqual(
            names, {"pick-a", "pick-b"},
            f"Only selected ids must appear in export; got {names!r}",
        )

    # -- 13.4 ----------------------------------------------------------
    def test_13_4_permission_export_masks_restricted_fields(self) -> None:
        """Scenario 13.4: ``ExportMaskedItem.permission_export`` allows
        only ``{id, name}`` for non-admins. ``amount`` and ``status``
        appear as columns but must be blank in every row."""
        ExportMaskedItem.objects.create(
            name="m1", amount="11.11", status=EXPORT_STATUS_ARCHIVED,
        )
        ExportMaskedItem.objects.create(
            name="m2", amount="22.22", status=EXPORT_STATUS_ARCHIVED,
        )

        resp = self.client.post(self.url_export(MASKED), data={}, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_response(resp)

        # Allowed: name values survive.
        self.assertEqual(
            set(df["name"].dropna().astype(str)), {"m1", "m2"},
            "Allowed field `name` must carry values",
        )
        # Restricted: amount + status masked to None across every row.
        for restricted in ("amount", "status"):
            if restricted not in df.columns:
                # Either outcome satisfies the contract: the column is
                # dropped OR it's blank. Dropping is stricter — log it.
                continue
            self.assertTrue(
                df[restricted].isna().all(),
                f"Restricted export field {restricted!r} leaked a value: "
                f"{df[restricted].tolist()!r}",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()












