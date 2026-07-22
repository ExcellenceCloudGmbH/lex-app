"""Export fallback keeps aware datetimes Excel-safe.

Intent: when the export endpoint falls back to pandas ``DataFrame.to_excel``,
timezone-aware ORM datetimes under ``USE_TZ=True`` must still export as the
same customer-visible instant instead of crashing xlsxwriter or silently
showing the raw UTC wall-clock. Cluster 13f — scenario 13.31. Type: E.
Covers: lex/api/views/file_operations/ModelExport.py.
Run: python -m lex pytest lex/test_project/tests/exports/test_13f_pandas_timezone_export.py -v
"""

from __future__ import annotations

import datetime as _dt
import unittest
from unittest.mock import patch

import pandas as pd
import pytest
from django.test import override_settings
from django.utils import timezone
from lex.api.views.file_operations.ModelExport import ModelExportView

from .models import ALL_MODELS, FAST, FastExportItem
from .test_13a_legacy_export import _assert_msg, _read_xlsx_response
from .test_13e_streaming_fast_export import _StreamingFastPathTestBase, _ag_flat_payload

pytestmark = pytest.mark.exports


@override_settings(TIME_ZONE="Europe/Berlin", USE_TZ=True)
class TestCluster13f_PandasTimezoneExport(_StreamingFastPathTestBase):
    """Cluster 13f: pandas fallback must flatten aware datetimes for Excel."""

    e2e_models = ALL_MODELS

    def test_13_31_pandas_fallback_localizes_aware_datetimes_before_excel_write(self) -> None:
        """
        Scenario 13.31: AG export with a timezone-aware DateTimeField survives the pandas fallback.
        Given: A ``FastExportItem`` row with an aware UTC ``happened_at`` value.
        When: Both streaming fast paths are unavailable and ``post()`` falls back to pandas ``to_excel``.
        Then: The workbook still returns 200 and the exported datetime is a naive Europe/Berlin wall-clock.
        """
        aware_utc = _dt.datetime(2024, 6, 1, 12, 0, tzinfo=_dt.timezone.utc)
        FastExportItem.objects.create(name="tz-row", happened_at=aware_utc)

        payload = _ag_flat_payload([
            {"dataKey": "name", "headerName": "Name"},
            {"dataKey": "happened_at", "headerName": "Happened At"},
        ])

        with patch.object(ModelExportView, "_try_stream_universal_fast_export", return_value=None), patch.object(
            ModelExportView, "_try_stream_flat_fast_export", return_value=None,
        ):
            resp = self.client.post(self.url_export(FAST), data=payload, format="json")

        self.assertEqual(resp.status_code, 200, _assert_msg(resp))

        df = _read_xlsx_response(resp)
        self.assertEqual(
            len(df), 1,
            f"Expected the fallback workbook to contain exactly one row; got {len(df)}",
        )

        actual_value = df.iloc[0]["Happened At"]
        self.assertFalse(
            pd.isna(actual_value),
            "The exported datetime cell must be populated after pandas fallback export",
        )

        actual_dt = pd.Timestamp(actual_value).to_pydatetime()
        expected_dt = timezone.localtime(aware_utc).replace(tzinfo=None)

        self.assertIsNone(
            actual_dt.tzinfo,
            f"Excel export must strip timezone info before writing; got {actual_dt!r}",
        )
        self.assertEqual(
            actual_dt, expected_dt,
            "Pandas fallback must preserve the same customer-visible instant in the active display timezone",
        )
        self.assertNotEqual(
            actual_dt, aware_utc.replace(tzinfo=None),
            "The fallback must not simply drop tzinfo and leave the UTC wall-clock unchanged",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
