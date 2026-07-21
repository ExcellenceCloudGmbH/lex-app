"""Cluster 13f: ``_to_excel_naive`` — timezone stripping for the legacy Excel path.

Intent: Under ``USE_TZ=True`` the ORM yields timezone-aware (UTC) datetimes.
``xlsxwriter`` refuses them (``"Excel does not support datetimes with timezones"``).
:func:`_to_excel_naive` must convert any aware datetime value to its wall-clock in
the configured display timezone (``settings.TIME_ZONE``) and strip the ``tzinfo``
before the DataFrame is written with ``pandas.to_excel``. Under ``USE_TZ=False``
the values are already naive and the function is a no-op.

Regression: without ``_to_excel_naive``, any legacy export that includes a
datetime column under ``USE_TZ=True`` would raise a ``ValueError`` inside
``xlsxwriter`` ("Excel does not support datetimes with timezones") and the
endpoint would return a 500 instead of the expected xlsx.

Cluster 13f — scenarios 13.31–13.36. Type: U (13.31–13.35) + E (13.36).
Covers: lex/api/views/file_operations/ModelExport.py (_to_excel_naive).
Run: python -m lex pytest lex/test_project/tests/exports/test_13f_tz_naive_excel.py -v
"""

from __future__ import annotations

import datetime as _dt
import unittest
from io import BytesIO
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from django.test import SimpleTestCase, override_settings

from lex.api.views.file_operations.ModelExport import _to_excel_naive
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, ITEM, ExportItem
from .test_13a_legacy_export import _assert_msg

pytestmark = pytest.mark.exports

UTC = ZoneInfo("UTC")


# ---------------------------------------------------------------------------
# Pure-unit scenarios 13.31–13.35 (no DB, no HTTP)
# ---------------------------------------------------------------------------


class TestCluster13f_ToExcelNaiveUnit(SimpleTestCase):
    """Cluster 13f (unit): ``_to_excel_naive`` pure-logic contract.

    Exercises every distinct branch of the function without requiring a
    database or HTTP client.
    """

    # -- 13.31 ------------------------------------------------------------------
    def test_13_31_empty_dataframe_is_returned_unchanged(self) -> None:
        """
        Scenario 13.31: empty DataFrame → returned unchanged.
        Given:  an empty DataFrame (zero rows, zero columns)
        When:   _to_excel_naive is called
        Then:   the returned DataFrame is still empty; no exception is raised
        """
        df = pd.DataFrame()
        result = _to_excel_naive(df)
        self.assertTrue(
            result.empty,
            "_to_excel_naive must return an empty DataFrame unchanged, "
            f"but got shape {result.shape}",
        )

    # -- 13.32 ------------------------------------------------------------------
    def test_13_32_non_datetime_columns_pass_through_unchanged(self) -> None:
        """
        Scenario 13.32: DataFrame with only non-datetime columns → all values
        preserved.
        Given:  a DataFrame whose columns hold int, float, bool, and str values
        When:   _to_excel_naive is called
        Then:   every cell value is identical to the input (no coercion)
        """
        data = {
            "int_col": [1, 2, 3],
            "float_col": [1.1, 2.2, 3.3],
            "str_col": ["a", "b", "c"],
        }
        df_in = pd.DataFrame(data)
        df_out = _to_excel_naive(df_in.copy())

        for col in data:
            for i, (expected, actual) in enumerate(
                zip(df_in[col].tolist(), df_out[col].tolist())
            ):
                self.assertEqual(
                    expected,
                    actual,
                    f"Non-datetime column {col!r}[{i}] must not be modified; "
                    f"expected {expected!r}, got {actual!r}",
                )

    # -- 13.33 ------------------------------------------------------------------
    def test_13_33_object_column_naive_datetime_is_unchanged(self) -> None:
        """
        Scenario 13.33: object column containing a naive datetime → value
        unchanged (no-op path inside ``_strip``).
        Given:  a DataFrame with an object column holding naive datetime objects
        When:   _to_excel_naive is called
        Then:   the datetime values are returned as-is (tzinfo is already None)
        """
        naive_dt = _dt.datetime(2024, 6, 15, 10, 30, 0)
        df = pd.DataFrame({"ts": [naive_dt, naive_dt]})
        df_out = _to_excel_naive(df.copy())

        for val in df_out["ts"]:
            self.assertEqual(
                val,
                naive_dt,
                f"Naive datetime must be returned unchanged; got {val!r}",
            )
            self.assertIsNone(
                getattr(val, "tzinfo", None),
                f"Naive datetime must have no tzinfo after _to_excel_naive; got {val!r}",
            )

    # -- 13.34 ------------------------------------------------------------------
    @override_settings(USE_TZ=True, TIME_ZONE="Europe/Berlin")
    def test_13_34_object_column_aware_datetime_is_stripped(self) -> None:
        """
        Scenario 13.34: object column containing a UTC-aware Python datetime →
        converted to the display timezone wall-clock and tzinfo stripped.
        Given:  a DataFrame with an object column holding a UTC-aware datetime
                (2024-06-15 10:00:00 UTC = 2024-06-15 12:00:00 Berlin summer
                time, CEST = UTC+2)
        When:   _to_excel_naive is called with TIME_ZONE="Europe/Berlin"
        Then:   the resulting cell is a naive datetime at 12:00:00 (Berlin
                wall-clock) with no tzinfo
        """
        # 10:00 UTC in summer = 12:00 Berlin (CEST = UTC+2)
        aware_utc = _dt.datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)
        df = pd.DataFrame({"ts": [aware_utc]})
        df_out = _to_excel_naive(df.copy())

        result_val = df_out["ts"].iloc[0]
        self.assertIsNone(
            getattr(result_val, "tzinfo", None),
            f"tzinfo must be stripped after _to_excel_naive; got {result_val!r}",
        )
        self.assertIsInstance(
            result_val,
            _dt.datetime,
            f"Value must remain a datetime object; got {type(result_val).__name__}",
        )
        # The wall-clock in Berlin for 10:00 UTC (CEST, UTC+2) is 12:00.
        self.assertEqual(
            (result_val.hour, result_val.minute),
            (12, 0),
            f"UTC 10:00 should appear as 12:00 Berlin (summer, CEST); "
            f"got {result_val:%H:%M}",
        )

    # -- 13.35 ------------------------------------------------------------------
    @override_settings(USE_TZ=True, TIME_ZONE="Europe/Berlin")
    def test_13_35_datetimetzdt_column_is_stripped(self) -> None:
        """
        Scenario 13.35: pandas ``DatetimeTZDtype`` column → converted to the
        display timezone and stripped of tzinfo.
        Given:  a DataFrame whose column has ``dtype=datetime64[us, UTC]``
                (the dtype Django's ORM+pandas produce under USE_TZ=True when
                values are read via ``.values()`` into a DataFrame)
        When:   _to_excel_naive is called
        Then:   the column dtype becomes timezone-naive ``datetime64[us]`` and
                the stored wall-clock values reflect the configured display
                timezone (summer: UTC+2 / CEST; winter: UTC+1 / CET)
        """
        # Build a UTC-aware pandas Series: tz_localize() on a naive Series.
        # Mimics what pd.read_sql / pd.DataFrame(queryset.values()) yields
        # for a DateTimeField when USE_TZ=True.
        aware_series = pd.to_datetime(
            ["2024-06-15T10:00:00", "2024-12-15T09:00:00"]
        ).tz_localize("UTC")
        df = pd.DataFrame({"ts": aware_series})

        self.assertIsInstance(
            df["ts"].dtype,
            pd.DatetimeTZDtype,
            "Precondition: input column must have DatetimeTZDtype",
        )

        df_out = _to_excel_naive(df.copy())

        # Post-condition: dtype is now timezone-naive.
        self.assertNotIsInstance(
            df_out["ts"].dtype,
            pd.DatetimeTZDtype,
            f"DatetimeTZDtype column must become tz-naive after _to_excel_naive; "
            f"got dtype={df_out['ts'].dtype!r}",
        )
        # Values are in the display timezone (Berlin):
        # 2024-06-15 10:00 UTC → 12:00 CEST (UTC+2)
        # 2024-12-15 09:00 UTC → 10:00 CET  (UTC+1)
        summer_val: _dt.datetime = df_out["ts"].iloc[0].to_pydatetime()
        winter_val: _dt.datetime = df_out["ts"].iloc[1].to_pydatetime()

        self.assertIsNone(
            summer_val.tzinfo,
            f"Summer value must have no tzinfo; got {summer_val!r}",
        )
        self.assertEqual(
            summer_val.hour,
            12,
            f"2024-06-15 10:00Z → Berlin 12:00 CEST; got {summer_val!r}",
        )
        self.assertIsNone(
            winter_val.tzinfo,
            f"Winter value must have no tzinfo; got {winter_val!r}",
        )
        self.assertEqual(
            winter_val.hour,
            10,
            f"2024-12-15 09:00Z → Berlin 10:00 CET; got {winter_val!r}",
        )


# ---------------------------------------------------------------------------
# Integration scenario 13.36 (legacy export endpoint)
# ---------------------------------------------------------------------------


class TestCluster13f_LegacyExportWithAwareDatetime(E2ETestCase):
    """Cluster 13f (integration): legacy export endpoint tolerates tz-aware
    datetimes in the DataFrame under ``USE_TZ=True``.

    Regression: without ``_to_excel_naive`` the ``xlsxwriter`` writer raises
    ``ValueError: Excel does not support datetimes with timezones`` and the
    endpoint returns HTTP 500.  With the fix the xlsxwriter call succeeds and
    the client receives a valid xlsx FileResponse.
    """

    e2e_models = ALL_MODELS

    # -- 13.36 ------------------------------------------------------------------
    def test_13_36_legacy_export_succeeds_with_tz_aware_dataframe(self) -> None:
        """
        Scenario 13.36: legacy export endpoint with a tz-aware datetime column
        in the DataFrame returns HTTP 200 and a readable xlsx — no xlsxwriter
        ValueError.

        Given:  an ExportItem row exists in the DB (queryset is non-empty)
                AND ``filter_and_mask_data_for_export`` is patched to return a
                DataFrame whose datetime column is timezone-aware UTC (mimicking
                what ``USE_TZ=True`` ORM output looks like once placed into a
                pandas object-dtype column)
        When:   the client POSTs to the legacy export endpoint (no ``ag_export``
                key → the ``else`` branch in ``post()`` runs)
        Then:   the response status is HTTP 200
                AND the response body is a streaming FileResponse (xlsx)
                AND the xlsx parses without error and contains at least one row
        """
        aware_dt = _dt.datetime(2024, 6, 15, 10, 0, 0, tzinfo=UTC)

        # Mimic what filter_and_mask_data_for_export returns under USE_TZ=True:
        # Python datetime objects with tzinfo set in an object-dtype column.
        tz_aware_df = pd.DataFrame(
            [{"id": 1, "name": "probe", "happened_at": aware_dt}]
        )

        ExportItem.objects.create(name="probe")

        with patch(
            "lex.api.views.file_operations.ModelExport."
            "ModelExportView.filter_and_mask_data_for_export",
            return_value=tz_aware_df,
        ):
            resp = self.client.post(self.url_export(ITEM), data={}, format="json")

        self.assertEqual(
            resp.status_code,
            200,
            "Legacy export must return HTTP 200 when the DataFrame contains "
            f"tz-aware datetimes (got {_assert_msg(resp)}). Without "
            "_to_excel_naive this path raises ValueError inside xlsxwriter.",
        )
        self.assertTrue(
            resp.streaming,
            "Response must be a streaming FileResponse (xlsx binary); "
            f"got streaming={resp.streaming!r}",
        )
        # Confirm the xlsx file is valid and non-empty.
        buf = BytesIO(b"".join(resp.streaming_content))
        buf.seek(0)
        df_result = pd.read_excel(buf)
        self.assertGreater(
            len(df_result),
            0,
            "xlsx produced by the export must contain at least one data row; "
            "an empty or malformed file means _to_excel_naive did not apply",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
