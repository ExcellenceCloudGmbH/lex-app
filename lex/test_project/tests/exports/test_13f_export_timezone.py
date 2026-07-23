"""Cluster 13f — Excel export renders datetimes in the requester's browser zone.

Intent: Excel has no timezone type, so an export must bake a wall-clock into each
datetime cell at generation time. Under USE_TZ=True the ORM holds aware UTC, so a
naive dump shows the UTC wall-clock (e.g. 09:00 instead of the 11:00 a Berlin user
sees in the grid). The export accepts the requester's browser timezone and renders
every datetime in it — on BOTH write paths: the legacy pandas path
(``_to_excel_naive``) and the streaming/fast xlsxwriter path
(``_normalize_cell_value``) — falling back to ``settings.TIME_ZONE``. A regression
here means exported spreadsheets show times shifted by the viewer's offset.

Cluster 13f — scenarios 13.31–13.33. Type: U.
Covers: lex/api/views/file_operations/ModelExport.py
        (_resolve_export_zone, _to_excel_naive, ModelExportView._normalize_cell_value).
Run: python -m lex pytest lex/test_project/tests/exports/test_13f_export_timezone.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pandas as pd
import pytest
from django.test import SimpleTestCase, override_settings

from lex.api.views.file_operations.ModelExport import (
    ModelExportView,
    _resolve_export_zone,
    _to_excel_naive,
)

pytestmark = pytest.mark.exports

# 09:00Z = 11:00 Berlin (summer, +2) = 05:00 New York (summer, −4)
INSTANT = datetime(2026, 7, 20, 9, 0, 0, tzinfo=dt_timezone.utc)


class TestCluster13f_ExportTimezone(SimpleTestCase):
    """Cluster 13f: exported datetimes use the requester's browser zone."""

    def _legacy_hour(self, zone) -> int:
        """Hour the legacy pandas path (_to_excel_naive) writes for INSTANT."""
        df = pd.DataFrame({"happened_at": [INSTANT], "name": ["x"]})
        out = _to_excel_naive(df, zone)
        cell = pd.to_datetime(out["happened_at"].iloc[0])
        self.assertIsNone(cell.tzinfo, "Excel cell must be tz-naive.")
        return cell.hour

    def _stream_hour(self, zone) -> int:
        """Hour the streaming path (_normalize_cell_value) writes for INSTANT."""
        view = ModelExportView.__new__(ModelExportView)  # no HTTP init needed
        view._export_target_zone = zone
        cell = view._normalize_cell_value(INSTANT)
        self.assertIsNone(cell.tzinfo, "Excel cell must be tz-naive.")
        return cell.hour

    def test_13_31_export_renders_datetimes_in_requested_berlin_zone(self) -> None:
        """
        Scenario 13.31: ?timezone=Europe/Berlin renders 09:00Z as 11:00 — on both
        the legacy and streaming write paths.
        """
        self.assertEqual(self._legacy_hour("Europe/Berlin"), 11, "legacy path → 11:00 Berlin")
        self.assertEqual(self._stream_hour("Europe/Berlin"), 11, "streaming path → 11:00 Berlin")

    def test_13_32_export_renders_datetimes_in_requested_ny_zone(self) -> None:
        """
        Scenario 13.32: the same instant exported for New York shows 05:00 — the
        zone is per-request, not fixed. Both paths agree.
        """
        self.assertEqual(self._legacy_hour("America/New_York"), 5, "legacy path → 05:00 NY")
        self.assertEqual(self._stream_hour("America/New_York"), 5, "streaming path → 05:00 NY")

    @override_settings(TIME_ZONE="Europe/Berlin")
    def test_13_33_no_or_invalid_zone_falls_back_to_settings(self) -> None:
        """
        Scenario 13.33: no/invalid timezone → settings.TIME_ZONE (Berlin), no error.
        """
        self.assertEqual(_resolve_export_zone("Europe/Berlin").key, "Europe/Berlin")
        self.assertEqual(_resolve_export_zone(None).key, "Europe/Berlin")   # settings fallback
        self.assertEqual(_resolve_export_zone("Not/AZone").key, "Europe/Berlin")  # invalid → fallback
        self.assertEqual(self._legacy_hour(None), 11, "legacy: no tz → settings.TIME_ZONE")
        self.assertEqual(self._stream_hour(None), 11, "streaming: no tz → settings.TIME_ZONE")
        self.assertEqual(self._legacy_hour("Not/AZone"), 11, "legacy: invalid tz → safe fallback")
