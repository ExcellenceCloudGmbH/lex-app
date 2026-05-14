"""
Cluster 13d: Export endpoint — auth & field-mask edge cases.

Two scenarios:

* 13.11 — Unauthenticated POST ``/api/<model>/export`` → 401/403, no
  ``.xlsx`` bytes. Locks in the ``permission_classes = [HasAPIKey |
  IsAuthenticated]`` gate on :class:`ModelExportView`.
* 13.12 — Non-uniform per-object ``permission_export`` forces
  :meth:`ModelExportView._compute_uniform_export_mask` to return
  ``None``; the slow per-row path
  (:meth:`ModelExportView._apply_export_mask_to_ag_rows`) runs and
  masks each row according to its own permission result.
"""

from __future__ import annotations

import unittest

from lex.core.models.LexModel import PermissionResult
from lex.tests.e2e._e2e_test_case import E2ETestCase
from rest_framework import status
from rest_framework.test import APIClient

from .models import (
    ALL_MODELS,
    EXPORT_STATUS_ACTIVE,
    EXPORT_STATUS_ARCHIVED,
    ITEM,
    MASKED,
    ExportItem,
    ExportMaskedItem,
)
from .test_13a_legacy_export import _assert_msg, _read_xlsx_response


class TestCluster13d_AuthAndFieldMask(E2ETestCase):
    """Auth gate (13.11) + non-uniform per-row export mask (13.12)."""

    e2e_models = ALL_MODELS

    # -- 13.11 ---------------------------------------------------------
    def test_13_11_unauthenticated_post_is_rejected(self) -> None:
        """Scenario 13.11: POST without auth → 401/403; no streamed
        body. ``ModelExportView.permission_classes`` is
        ``[HasAPIKey | IsAuthenticated]`` — anonymous clients must be
        rejected before the sheet is ever assembled.
        """
        # Seed at least one row so a green path would return 200.
        ExportItem.objects.create(
            name="auth-gate", amount="1.00", status=EXPORT_STATUS_ACTIVE,
        )

        anon = APIClient()  # fresh client, not force_login'd.

        resp = anon.post(self.url_export(ITEM), data={}, format="json")

        self.assertIn(
            resp.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
            f"Unauthenticated export must be rejected with 401/403; "
            f"got {resp.status_code} — a successful response here is a "
            f"wide-open data-exfiltration bug",
        )
        # No ``.xlsx`` bytes on an auth failure.
        self.assertFalse(
            getattr(resp, "streaming", False),
            "Auth-rejected response must not stream any file content",
        )

    # -- 13.12 ---------------------------------------------------------
    def test_13_12_non_uniform_permission_export_runs_slow_mask(self) -> None:
        """Scenario 13.12: when ``permission_export`` depends on the
        instance (not just the caller), ``_compute_uniform_export_mask``
        returns ``None`` and the slow per-row mask fires. Each row is
        masked according to its OWN permission result.

        Fixture: two :class:`ExportMaskedItem` rows.

        * ``ExportMaskedItem.permission_export`` for our non-admin
          session user returns ``allow_fields({"id", "name"})`` —
          ``amount`` / ``status`` must be blanked in the sheet.

        We then monkey-patch the model so the second row additionally
        allows ``{"amount"}`` — the uniform fast path sees two
        different permission results and bails to the slow path. The
        sheet's ``amount`` column should be non-empty for the second
        row and empty for the first.
        """
        # Seed two rows with distinguishable amounts.
        row_a = ExportMaskedItem.objects.create(
            name="masked-A", amount="11.00", status=EXPORT_STATUS_ACTIVE,
        )
        row_b = ExportMaskedItem.objects.create(
            name="masked-B", amount="22.00", status=EXPORT_STATUS_ARCHIVED,
        )

        original = ExportMaskedItem.permission_export

        def _per_row(self, uc):
            if uc.is_superuser or "admin" in uc.groups:
                return PermissionResult.allow_all("admin — everything")
            # Row B additionally allows ``amount`` — heterogeneous
            # permissions force the slow path.
            if self.name == "masked-B":
                return PermissionResult.allow_fields(
                    {"id", "name", "amount"},
                    "row B — name + amount",
                )
            return PermissionResult.allow_fields(
                {"id", "name"}, "row A — name only",
            )

        ExportMaskedItem.permission_export = _per_row
        try:
            # Legacy (non-AG) POST → the ``filter_and_mask_data_for_export``
            # path, which consults ``_compute_uniform_export_mask`` first
            # and falls back to the per-row loop on None.
            resp = self.client.post(
                self.url_export(MASKED), data={}, format="json",
            )
            self.assertEqual(resp.status_code, 200, _assert_msg(resp))

            df = _read_xlsx_response(resp)
            self.assertEqual(
                len(df), 2,
                f"Expected both masked rows in the sheet; got {len(df)}",
            )

            # Re-key by name so we can assert per-row masking.
            by_name = {str(r["name"]): r for _, r in df.iterrows() if "name" in r}
            self.assertIn("masked-A", by_name)
            self.assertIn("masked-B", by_name)

            def _is_blank(val):
                import pandas as pd

                return val is None or (isinstance(val, float) and pd.isna(val)) or val == ""

            # Row A — amount NOT allowed → blank in sheet.
            if "amount" in df.columns:
                self.assertTrue(
                    _is_blank(by_name["masked-A"]["amount"]),
                    f"Row A amount should be masked (blank) under "
                    f"per-row permission; got {by_name['masked-A']['amount']!r}",
                )
                # Row B — amount allowed → populated.
                self.assertFalse(
                    _is_blank(by_name["masked-B"]["amount"]),
                    "Row B amount should survive under its row-specific "
                    "permission result — the slow per-row path did not "
                    "consult the per-object permission_export",
                )
        finally:
            ExportMaskedItem.permission_export = original


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

