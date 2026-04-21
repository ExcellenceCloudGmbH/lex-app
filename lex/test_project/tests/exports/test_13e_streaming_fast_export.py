"""
Cluster 13e: Streaming fast-path exports — universal & flat.

Fills the coverage gap left by Clusters 13a / 13b / 13c. Those
files all drive models (``ExportItem`` / ``ExportMaskedItem``)
that override ``permission_export``, which makes
:meth:`ModelExportView._has_default_export_permissions` return
``False``. That single check guards the three streaming fast paths
inside :class:`~lex.api.views.file_operations.ModelExport.ModelExportView`:

* :meth:`ModelExportView._try_stream_universal_fast_export`
* :meth:`ModelExportView._try_stream_flat_fast_export`
* :meth:`ModelExportView._try_build_flat_fast_export_dataframe`

With it returning ``False``, every previous cluster-13 test fell
through to the pandas fallback and ~400 lines of production code
sat uncovered. This file exercises those paths end-to-end by
driving :class:`~.models.FastExportItem` (no ``permission_export``
override) and granting the test user the ``export`` Keycloak scope
so the default ``LexModel.permission_export`` returns
``allow_all``.

Method coverage added here (all new vs 13a–13c):

* :meth:`ModelExportView._classify_export_columns`
* :meth:`ModelExportView._make_attr_resolver`
* :meth:`ModelExportView._make_db_resolver`
* :meth:`ModelExportView._try_stream_universal_fast_export`
  (values fast path **and** instance-hydration path)
* :meth:`ModelExportView._try_stream_flat_fast_export`
* :meth:`ModelExportView._resolve_ag_column_layout`
* :meth:`ModelExportView._build_fk_display_maps`
* :meth:`ModelExportView._normalize_cell_value`
  (bool / int / float / datetime / date / collection / str branches)
"""

from __future__ import annotations

import datetime as _dt
import unittest
from decimal import Decimal
from unittest.mock import patch

from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import (
    ALL_MODELS,
    FAST,
    ExportCategory,
    FastExportItem,
)
from io import BytesIO

import pandas as pd

from .test_13a_legacy_export import _assert_msg


def _read_xlsx_stream(resp) -> pd.DataFrame:
    """Read an xlsx produced by the **streaming** export paths.

    Unlike the legacy path, :meth:`_try_stream_universal_fast_export`
    and :meth:`_try_stream_flat_fast_export` write via raw
    ``xlsxwriter`` with **no** DataFrame index column. Using the
    shared ``_read_xlsx_response`` (which passes ``index_col=0``)
    would silently swallow the first requested column as the index.
    """
    buf = BytesIO(b"".join(resp.streaming_content))
    buf.seek(0)
    return pd.read_excel(buf)


def _ag_flat_payload(columns: list[dict], end_row: int = 1000) -> dict:
    return {
        "ag_export": {
            "request": {
                "startRow": 0,
                "endRow": end_row,
                "rowGroupCols": [],
                "groupKeys": [],
                "pivotCols": [],
                "pivotMode": False,
                "valueCols": [],
                "sortModel": [],
                "filterModel": {},
            },
            "columns": columns,
            "includeColumnLabels": True,
        }
    }


class _StreamingFastPathTestBase(E2ETestCase):
    """Shared setUp that grants the ``export`` Keycloak scope.

    The default :meth:`lex.core.models.LexModel.LexModel.permission_export`
    returns :meth:`PermissionResult.deny` unless ``"export"`` is in the
    caller's ``UserContext.keycloak_scopes``. Session-authenticated
    tests don't carry Keycloak permissions so we patch
    :meth:`UserContext._resolve_keycloak_scopes` to synthesise the
    minimum set needed for an allowed export. Everything else about
    the test client (login, session, url reversing) stays untouched.
    """

    e2e_models = ALL_MODELS

    def setUp(self):
        super().setUp()
        # Grant the scopes the default LexModel permission methods look
        # for. ``export`` is the one that matters for this cluster;
        # ``read`` / ``list`` are included so unrelated middleware
        # paths don't fight us.
        self._scope_patch = patch(
            "lex.core.models.LexModel.UserContext._resolve_keycloak_scopes",
            return_value={"export", "read", "list", "edit"},
        )
        self._scope_patch.start()
        self.addCleanup(self._scope_patch.stop)


class TestCluster13e_UniversalStreamingExport(_StreamingFastPathTestBase):
    """``_try_stream_universal_fast_export`` — the production hot path."""

    # -- 13.13 ---------------------------------------------------------
    def test_13_13_all_db_columns_values_fast_path(self) -> None:
        """All-DB columns → universal stream takes the ``use_values``
        fast path (no model hydration).

        Covers:
        * :meth:`_classify_export_columns` classifying every column as
          ``"db"`` (``attr_count == 0``).
        * :meth:`_build_fk_display_maps` mapping the FK pk → readable
          ``str(category)``.
        * :meth:`_normalize_cell_value` boolean + int + Decimal→str
          + datetime/date branches via a single row carrying diverse
          types.
        * :meth:`_resolve_ag_column_layout` honoring ``headerName`` and
          ``dataKey``.
        """
        cat_alpha = ExportCategory.objects.create(name="alpha")
        cat_beta = ExportCategory.objects.create(name="beta")
        FastExportItem.objects.create(
            name="row-a", amount=Decimal("10.50"), count=3, active=True,
            happened_on=_dt.date(2024, 1, 15),
            happened_at=_dt.datetime(2024, 1, 15, 10, 30, 0),
            category=cat_alpha,
        )
        FastExportItem.objects.create(
            name="row-b", amount=Decimal("20.00"), count=5, active=False,
            happened_on=None, happened_at=None,
            category=cat_beta,
        )
        FastExportItem.objects.create(
            name="row-c", amount=Decimal("30.00"), count=7, active=True,
            happened_on=_dt.date(2024, 3, 1),
            happened_at=_dt.datetime(2024, 3, 1, 8, 0, 0),
            category=None,
        )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "amount", "headerName": "Amount"},
            {"dataKey": "count", "headerName": "Count"},
            {"dataKey": "active", "headerName": "Active"},
            {"dataKey": "happened_on", "headerName": "Happened On"},
            {"dataKey": "happened_at", "headerName": "Happened At"},
            {"dataKey": "category", "headerName": "Category"},
        ])

        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        # Column order + headerName rename survived the layout resolver.
        self.assertEqual(
            list(df.columns),
            ["Name", "Amount", "Count", "Active", "Happened On",
             "Happened At", "Category"],
            f"Layout must honour requested order + headerName; got "
            f"{list(df.columns)!r}",
        )
        # All three rows written (values fast path breaks out of the
        # loop on ``written >= end_row`` — endRow was 1000 here).
        self.assertEqual(
            len(df), 3,
            f"Expected 3 rows in universal-stream export, got {len(df)}",
        )
        # Name + Count survive unchanged.
        self.assertEqual(
            set(df["Name"].astype(str)), {"row-a", "row-b", "row-c"},
        )
        self.assertEqual(
            sorted(df["Count"].dropna().astype(int).tolist()), [3, 5, 7],
        )
        # Booleans round-trip via ``write_boolean``.
        active_values = set(df["Active"].dropna().astype(bool).tolist())
        self.assertEqual(
            active_values, {True, False},
            "Boolean column must round-trip via write_boolean; "
            f"got {active_values!r}",
        )
        # FK display map fired — pk replaced by ``str(category)``.
        categories = set(df["Category"].dropna().astype(str))
        self.assertIn("Cat<alpha>", categories)
        self.assertIn("Cat<beta>", categories)
        for cat in categories:
            self.assertFalse(
                cat.strip().isdigit(),
                f"FK pk leaked into stream export as {cat!r} — "
                "_build_fk_display_maps did not populate",
            )

    # -- 13.14 ---------------------------------------------------------
    def test_13_14_computed_column_hydrates_instances(self) -> None:
        """Mixed DB + computed columns → universal stream takes the
        **instance-hydration** path (``use_values_fast_path = False``)
        because ``attr_count > 0``.

        Covers:
        * :meth:`_classify_export_columns` classifying ``display_label``
          as ``"attr"`` (not resolvable via ``_resolve_lookup``).
        * :meth:`_make_attr_resolver` reading a ``@property`` attribute.
        * :meth:`_make_db_resolver` walking the FK chain
          ``category__name`` under the hydration path.
        * ``select_related`` pre-warm so no N+1 fires per row.
        """
        cat_alpha = ExportCategory.objects.create(name="alpha")
        FastExportItem.objects.create(
            name="row-a", amount=Decimal("10.50"), count=2,
            category=cat_alpha,
        )
        FastExportItem.objects.create(
            name="row-b", amount=Decimal("20.25"), count=4,
            category=cat_alpha,
        )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            # Computed @property — forces classification="attr" and
            # the non-values hydration path.
            {"dataKey": "display_label", "headerName": "Label"},
            # FK traversal via dotted AG key; `_resolve_lookup` turns
            # this into db path ``category__name`` — forces a
            # ``select_related`` and exercises ``_make_db_resolver``.
            {"dataKey": "category.name", "headerName": "Cat Name"},
        ])

        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        self.assertEqual(len(df), 2, f"Expected 2 rows, got {len(df)}")
        # @property resolved per row via _make_attr_resolver.
        labels = set(df["Label"].dropna().astype(str))
        self.assertEqual(
            labels, {"row-a::10.50", "row-b::20.25"},
            f"display_label property must be resolved per row; got {labels!r}",
        )
        # FK-chain DB resolver walked category→name via select_related.
        cat_names = set(df["Cat Name"].dropna().astype(str))
        self.assertEqual(
            cat_names, {"alpha"},
            f"category.name should resolve to related model field; got {cat_names!r}",
        )

    # -- 13.15 ---------------------------------------------------------
    def test_13_15_short_description_resolver_falls_back_to_str(self) -> None:
        """``short_description`` has bespoke resolver semantics —
        when the model defines no attribute of that name it falls back
        to ``str(instance)``.

        Covers the ``short_description_resolver`` special case inside
        :meth:`_make_attr_resolver`.
        """
        FastExportItem.objects.create(name="sd-a", amount=Decimal("1.00"))
        FastExportItem.objects.create(name="sd-b", amount=Decimal("2.00"))

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "short_description", "headerName": "Short"},
        ])

        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        shorts = set(df["Short"].dropna().astype(str))
        # FastExportItem.__str__ returns ``f"Fast<{name}>"``.
        self.assertEqual(
            shorts, {"Fast<sd-a>", "Fast<sd-b>"},
            f"short_description must fall back to str(instance); got {shorts!r}",
        )

    # -- 13.16 ---------------------------------------------------------
    def test_13_16_callable_attribute_is_invoked(self) -> None:
        """Dotted attribute referring to a zero-arg method is called.

        Covers the ``callable(current)`` branch of the generic
        :meth:`_make_attr_resolver`.
        """
        FastExportItem.objects.create(
            name="calc-a", amount=Decimal("10"), count=3,
        )
        FastExportItem.objects.create(
            name="calc-b", amount=Decimal("5"), count=4,
        )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "compute_amount_times_count", "headerName": "Total"},
        ])

        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        totals = sorted(df["Total"].dropna().astype(float).tolist())
        # 10*3=30, 5*4=20.
        self.assertEqual(
            totals, [20.0, 30.0],
            f"Zero-arg callable must be invoked by resolver; got {totals!r}",
        )

    # -- 13.17 ---------------------------------------------------------
    def test_13_17_end_row_limit_caps_written_rows(self) -> None:
        """``endRow`` smaller than DB row count → stream loop breaks at
        the cap (``written >= end_row``)."""
        for i in range(5):
            FastExportItem.objects.create(
                name=f"cap-{i}", amount=Decimal("1.00"), count=i,
            )

        payload = _ag_flat_payload(
            [{"dataKey": "name", "headerName": "Name"}],
            end_row=2,
        )
        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        self.assertEqual(
            len(df), 2,
            f"endRow=2 must cap the stream; wrote {len(df)} rows",
        )

    # -- 13.18 ---------------------------------------------------------
    def test_13_18_empty_queryset_streaming_returns_404(self) -> None:
        """Empty DB through the streaming path → 404 JSON, not an empty
        xlsx.

        Covers the ``written == 0`` branch of
        :meth:`_try_stream_universal_fast_export` that unlinks the
        temp file and returns a ``JsonResponse`` 404.
        """
        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
        ])
        resp = self.client.post(self.url_export(FAST), data=payload, format="json")

        self.assertEqual(resp.status_code, 404, _assert_msg(resp))
        body = resp.json()
        self.assertIn(
            "error", body,
            f"Empty stream must return JSON error body; got {body!r}",
        )

    # -- 13.19 ---------------------------------------------------------
    def test_13_19_classify_returns_none_for_only_ag_internal_columns(self) -> None:
        """AG UI columns (``ag-Grid-SelectionColumn``) and synthetic
        ``__ag_*`` columns are skipped by
        :meth:`_classify_export_columns`. When those are the *only*
        columns, classification returns ``None`` and every streaming
        path bails — the endpoint falls back to the legacy pandas
        branch which exports all fields.
        """
        FastExportItem.objects.create(name="fallback", amount=Decimal("1.00"))

        payload = _ag_flat_payload([
            {"dataKey": "ag-Grid-SelectionColumn", "headerName": "Sel"},
            {"dataKey": "__ag_group_hierarchy_label", "headerName": "Grp"},
        ])
        resp = self.client.post(self.url_export(FAST), data=payload, format="json")
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        self.assertGreater(
            len(df), 0,
            "Legacy fallback must still produce a non-empty sheet when "
            "every AG column is synthetic",
        )


class TestCluster13e_FlatDbFastExport(_StreamingFastPathTestBase):
    """``_try_stream_flat_fast_export`` — the older DB-only streaming
    path. Normally ``_try_stream_universal_fast_export`` wins, so this
    path only runs when the universal path returns ``None``. We force
    the universal path to bail once so the flat path is exercised."""

    # -- 13.20 ---------------------------------------------------------
    def test_13_20_flat_fast_export_runs_when_universal_bails(self) -> None:
        """Patch the universal path to return ``None`` exactly once →
        :meth:`_try_stream_flat_fast_export` is picked up by the
        ``post`` orchestrator and streams the same flat export.

        Covers:
        * :meth:`_try_stream_flat_fast_export` full happy path.
        * :meth:`_resolve_ag_column_layout` called against
          ``effective_paths`` (not a DataFrame).
        * :meth:`_build_fk_display_maps` on the DB-only path.
        """
        cat_alpha = ExportCategory.objects.create(name="alpha")
        FastExportItem.objects.create(
            name="flat-a", amount=Decimal("10.00"), count=1,
            category=cat_alpha,
        )
        FastExportItem.objects.create(
            name="flat-b", amount=Decimal("20.00"), count=2,
            category=cat_alpha,
        )

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "amount", "headerName": "Amount"},
            {"dataKey": "category", "headerName": "Category"},
        ])

        # Force fall-through to the flat path.
        with patch(
            "lex.api.views.file_operations.ModelExport."
            "ModelExportView._try_stream_universal_fast_export",
            return_value=None,
        ):
            resp = self.client.post(
                self.url_export(FAST), data=payload, format="json",
            )
        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_stream(resp)
        self.assertEqual(
            list(df.columns), ["Name", "Amount", "Category"],
            f"Flat-fast layout must honour headerName order; got {list(df.columns)!r}",
        )
        self.assertEqual(
            set(df["Name"].astype(str)), {"flat-a", "flat-b"},
        )
        cats = set(df["Category"].dropna().astype(str))
        self.assertEqual(
            cats, {"Cat<alpha>"},
            f"Flat-fast FK display map must render readable names; got {cats!r}",
        )


class TestCluster13e_NormalizeCellValueDirect(_StreamingFastPathTestBase):
    """Unit-style assertions on :meth:`_normalize_cell_value`.

    Integration-level tests above only exercise the branches reachable
    via AG exports; this covers the remaining ones (``None``,
    timezone-aware datetime, collection → ``str(value)``) without
    stapling on a brittle multi-type fixture.
    """

    def test_13_21_normalize_cell_value_covers_every_branch(self) -> None:
        from lex.api.views.file_operations.ModelExport import ModelExportView

        view = ModelExportView()

        # None passes through unchanged.
        self.assertIsNone(view._normalize_cell_value(None))

        # Primitives pass through unchanged.
        self.assertEqual(view._normalize_cell_value("x"), "x")
        self.assertEqual(view._normalize_cell_value(42), 42)
        self.assertEqual(view._normalize_cell_value(3.14), 3.14)
        self.assertIs(view._normalize_cell_value(True), True)

        # Timezone-aware datetime → tz stripped.
        try:
            from zoneinfo import ZoneInfo
            aware = _dt.datetime(2024, 6, 1, 12, 0, tzinfo=ZoneInfo("UTC"))
        except Exception:  # pragma: no cover
            aware = _dt.datetime(2024, 6, 1, 12, 0, tzinfo=_dt.timezone.utc)
        normalised = view._normalize_cell_value(aware)
        self.assertIsNone(
            normalised.tzinfo,
            f"tz-aware datetime must be flattened; got {normalised!r}",
        )

        # Naive datetime untouched.
        naive = _dt.datetime(2024, 6, 1, 12, 0)
        self.assertEqual(view._normalize_cell_value(naive), naive)

        # Date + time pass through unchanged.
        self.assertEqual(
            view._normalize_cell_value(_dt.date(2024, 6, 1)),
            _dt.date(2024, 6, 1),
        )
        self.assertEqual(
            view._normalize_cell_value(_dt.time(10, 30)),
            _dt.time(10, 30),
        )

        # Collections → ``str(value)``.
        self.assertEqual(view._normalize_cell_value([1, 2, 3]), "[1, 2, 3]")
        self.assertEqual(view._normalize_cell_value((1, 2)), "(1, 2)")
        self.assertEqual(view._normalize_cell_value({"k": "v"}), "{'k': 'v'}")

        # Anything else → ``str(value)``. Decimal is the canonical
        # example we see in real exports.
        self.assertEqual(view._normalize_cell_value(Decimal("1.23")), "1.23")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()


