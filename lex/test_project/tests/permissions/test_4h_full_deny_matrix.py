"""
Cluster 4h: Full deny matrix — explicit ``permission_export`` deny
at the API endpoint.

Closes the last reachable gap in the Cluster-4 deny matrix. The
existing coverage already pins:

* read deny — 4.8 (Keycloak no-scope unit), 4.13 (per-row filter
  backend), 4.16 (pk_only respects denies), 4.18 (deny-all profile).
* edit deny — 4.4 (PATCH-restricted-field, BUG-010), 4.8b (Keycloak
  no-scope unit), 4.8c (edit scope ≠ read scope), 4.24 (mixin
  ``run_validation`` raises ``PermissionDenied``).
* create deny — 4.6 (non-admin POST 403, BUG-008), 4.26 (mixin
  ``permission_create`` raises ``PermissionDenied``).
* delete deny — 4.5 (non-admin DELETE 401/403).
* export deny (per-field) — 13.4 (uniform mask), 13.12 (per-row
  heterogeneous mask).
* export deny (Keycloak no-scope, unit) — 4.8b.
* unauthenticated export — 13.11.
* list deny (navigation pruning) — 10.14.

This file pins what the **export endpoint** does when a model
returns :meth:`PermissionResult.deny` from ``permission_export``
for the calling user. The framework's documented behaviour is to
union ``{id, created_by, edited_by}`` onto the empty allow-set in
:meth:`ModelExportView.get_exportable_fields_for_object`, so the
sheet still renders rows but every domain field is blanked. Pinning
this prevents an over-restrictive future change (a 403 might break
the compliance-ID export) and an over-permissive one (any column
suddenly leaking domain data through a denied permission would be
a P0 leak).

Scenario numbering: 4.40.
"""

from __future__ import annotations

import unittest
from io import BytesIO

import pandas as pd
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, EXPORT_DENIED, ExportDeniedItem

import pytest

pytestmark = pytest.mark.permissions


def _read_xlsx_response(resp) -> pd.DataFrame:
    """Collect a ``FileResponse`` into a DataFrame.

    Mirrors the reader in cluster 13's tests — ``ModelExportView.post``
    writes ``index=True`` so column 0 is the numeric index.
    """
    buf = BytesIO(b"".join(resp.streaming_content))
    buf.seek(0)
    return pd.read_excel(buf, index_col=0)


class TestCluster04h_ExportFullDeny(E2ETestCase):
    """``permission_export`` returns full deny — the export endpoint
    must still succeed (read is allowed, the row is selectable) but
    every domain column must be blank in every row.
    """

    e2e_models = ALL_MODELS

    # -- 4.40 ----------------------------------------------------------
    def test_4_40_export_full_deny_blanks_every_domain_field(self) -> None:
        """Scenario 4.40: ``permission_export`` returns
        :meth:`PermissionResult.deny` → the .xlsx the endpoint streams
        must contain rows (read is open) but **no** domain field may
        carry a value.

        The framework force-unions ``{id, created_by, edited_by}`` onto
        the allow-set in
        :meth:`ModelExportView.get_exportable_fields_for_object`, so
        the column ``name`` and ``payload`` (the model's two declared
        domain fields) must be blank in every row even though the
        underlying DB rows hold values.

        A regression that drops the union would 500 the response (no
        identifying columns left); a regression that leaks domain data
        through a denied permission is the P0 case this scenario
        guards against.
        """
        ExportDeniedItem.objects.create(name="row-a", payload="secret-a")
        ExportDeniedItem.objects.create(name="row-b", payload="secret-b")

        resp = self.client.post(
            self.url_export(EXPORT_DENIED), data={}, format="json",
        )
        self.assertEqual(
            resp.status_code, 200,
            f"Export must succeed (read is open); got {resp.status_code}",
        )

        df = _read_xlsx_response(resp)
        # Domain fields must be blank everywhere, even though the rows
        # themselves materialised (read is open).
        for restricted in ("name", "payload"):
            if restricted not in df.columns:
                # Column-dropped is the stricter outcome — also
                # acceptable.
                continue
            self.assertTrue(
                df[restricted].isna().all(),
                f"Denied export field {restricted!r} leaked values "
                f"despite permission_export deny: "
                f"{df[restricted].tolist()!r}",
            )
        # Sanity: rows themselves still appear (the deny doesn't drop
        # the row; only the per-field mask does).
        self.assertEqual(
            len(df), 2,
            "Export deny masks fields, not rows — both seeded rows "
            "must still surface (use permission_read to drop rows). "
            f"Got {len(df)} rows.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

