"""Cluster 13g — report Excel files hold display-zone wall-clock, both ways.

Intent: Excel has no timezone type, so ``to_excel`` raises on the aware
datetimes every DateTimeField now carries under USE_TZ=True — which crashed
every report writing through ``XLSXField.create_excel_file_from_dfs``.
``_excel_display_naive`` fixes the boundary the way ModelExport does (13f):
render aware values as the project display zone's wall clock (settings
TIME_ZONE, Berlin) and strip the tzinfo — across data columns, mixed object
columns, the (multi)index and column headers, since report pivots put dates in
all four. Reading is the mirror image: a cell parsed back from the file is
naive Berlin wall clock, and the moment it is assigned to a LexModel
DateTimeField the aware-on-assignment invariant (3g) restores the exact
original instant. A regression here either crashes report generation or ships
spreadsheets whose times are shifted by the viewer's offset.

Cluster 13g — scenarios 13.34–13.37. Type: U.
Covers: lex/core/fields/XLSX_field.py
        (_excel_display_naive, XLSXField.create_excel_file_from_dfs).
Run: python -m lex pytest lex/test_project/tests/exports/test_13g_xlsx_field_timezone.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from types import SimpleNamespace

import pandas as pd
import pytest
from django.test import SimpleTestCase, override_settings

from lex.core.fields.XLSX_field import XLSXField, _excel_display_naive

from .models import FastExportItem

pytestmark = pytest.mark.exports

# 09:00Z = 11:00 Berlin (summer, +02:00)
SUMMER_INSTANT = datetime(2026, 7, 20, 9, 0, 0, tzinfo=dt_timezone.utc)
# 23:00Z Dec 30 = 00:00 Dec 31 Berlin (winter, +01:00) — quarter-end day flip
WINTER_INSTANT = datetime(2026, 12, 30, 23, 0, 0, tzinfo=dt_timezone.utc)


@override_settings(USE_TZ=True, TIME_ZONE="Europe/Berlin")
class TestCluster13g_XlsxFieldTimezone(SimpleTestCase):
    """Cluster 13g: the report-file boundary renders and re-reads Berlin."""

    @staticmethod
    def _write_and_read_back(df: pd.DataFrame) -> pd.DataFrame:
        """Drive the public entry the way a report does and parse the file.

        Real callers hand ``create_excel_file_from_dfs`` a FieldFile as
        ``self``; storage is the external boundary, so a stub captures the
        ``save`` call and we read the returned in-memory workbook instead.
        """
        storage_stub = SimpleNamespace(save=lambda *args, **kwargs: None)
        excel_file = XLSXField.create_excel_file_from_dfs(
            storage_stub, "report.xlsx", [df],
        )
        return pd.read_excel(excel_file, sheet_name="Sheet", index_col=0)

    def test_13_34_aware_columns_render_display_zone_wall_clock(self) -> None:
        """
        Scenario 13.34: an aware datetime column becomes naive Berlin wall
        clock — including the winter case where the calendar day flips.
        Given: a tz-aware column holding 09:00Z (summer) and Dec 30 23:00Z (winter)
        When: normalized for Excel
        Then: cells read 11:00 and Dec 31 00:00 — naive, right day
        """
        df = pd.DataFrame({
            "happened_at": pd.to_datetime([SUMMER_INSTANT, WINTER_INSTANT], utc=True),
            "amount": [1.0, 2.0],
        })
        out = _excel_display_naive(df)
        summer, winter = out["happened_at"].iloc[0], out["happened_at"].iloc[1]
        self.assertIsNone(
            summer.tzinfo, "Excel cells must be tz-naive after normalization."
        )
        self.assertEqual(
            (summer.hour, summer.minute), (11, 0),
            f"09:00Z must render as the 11:00 Berlin wall clock, got {summer}.",
        )
        self.assertEqual(
            (winter.month, winter.day, winter.hour), (12, 31, 0),
            f"Dec 30 23:00Z is Berlin midnight Dec 31 — the quarter-end must "
            f"land on the right day, got {winter}.",
        )

    def test_13_35_object_column_index_and_headers_normalize(self) -> None:
        """
        Scenario 13.35: report pivots put datetimes everywhere — mixed object
        columns, the (multi)index and the column headers all normalize, and
        the caller's frame is left untouched.
        Given: aware datetimes in an object column, a DatetimeIndex, a
               MultiIndex level and the column headers
        When: normalized for Excel
        Then: every position is naive Berlin wall clock; the input frame
              still holds its aware values
        """
        naive = datetime(2026, 6, 30, 0, 0, 0)
        df = pd.DataFrame(
            {"mixed": [SUMMER_INSTANT, naive], "amount": [1.0, 2.0]},
            index=pd.DatetimeIndex(
                [pd.Timestamp(SUMMER_INSTANT), pd.Timestamp(WINTER_INSTANT)],
                name="report_date",
            ),
        )
        out = _excel_display_naive(df)
        self.assertEqual(
            (out["mixed"].iloc[0].hour, out["mixed"].iloc[0].tzinfo), (11, None),
            f"aware object-column cell must be naive Berlin, got {out['mixed'].iloc[0]}.",
        )
        self.assertEqual(
            out["mixed"].iloc[1], naive,
            "already-naive cells must pass through unchanged.",
        )
        self.assertIsNone(out.index.tz, "the index must lose its timezone.")
        self.assertEqual(
            (out.index[1].month, out.index[1].day), (12, 31),
            f"the winter index entry must land on Dec 31 Berlin, got {out.index[1]}.",
        )
        self.assertIsNotNone(
            df["mixed"].iloc[0].tzinfo,
            "the caller's frame must not be mutated by normalization.",
        )

        pivot = pd.DataFrame(
            [[1.0]],
            index=pd.MultiIndex.from_tuples(
                [("FundA", pd.Timestamp(WINTER_INSTANT))], names=["fund", "date"]
            ),
            columns=[pd.Timestamp(SUMMER_INSTANT)],
        )
        outp = _excel_display_naive(pivot)
        self.assertEqual(
            (outp.index[0][1].day, outp.index[0][1].tzinfo), (31, None),
            f"MultiIndex datetime levels must normalize, got {outp.index[0][1]}.",
        )
        self.assertEqual(
            (outp.columns[0].hour, outp.columns[0].tzinfo), (11, None),
            f"datetime column headers must normalize, got {outp.columns[0]}.",
        )

    def test_13_36_written_file_cells_read_berlin_wall_clock(self) -> None:
        """
        Scenario 13.36: end-to-end through the public entry — the .xlsx that
        create_excel_file_from_dfs produces holds Berlin wall-clock cells.
        Given: a report frame with aware datetimes (the post-USE_TZ reality
               that used to crash with "Excel does not support datetimes
               with timezones")
        When: written via create_excel_file_from_dfs and parsed back
        Then: the file's cells read 11:00 / Dec 31 00:00, tz-naive
        """
        df = pd.DataFrame({
            "name": ["summer", "winter"],
            "happened_at": pd.to_datetime([SUMMER_INSTANT, WINTER_INSTANT], utc=True),
        })
        read_back = self._write_and_read_back(df)
        cells = pd.to_datetime(read_back["happened_at"])
        self.assertIsNone(
            cells.iloc[0].tzinfo, "cells in the file must be tz-naive."
        )
        self.assertEqual(
            (cells.iloc[0].hour, cells.iloc[0].minute), (11, 0),
            f"the written file must show 11:00 Berlin for 09:00Z, got {cells.iloc[0]}.",
        )
        self.assertEqual(
            (cells.iloc[1].month, cells.iloc[1].day, cells.iloc[1].hour),
            (12, 31, 0),
            f"the winter quarter-end must be written as Dec 31 00:00 Berlin, "
            f"got {cells.iloc[1]}.",
        )

    def test_13_37_read_back_cell_restores_the_instant_on_assignment(self) -> None:
        """
        Scenario 13.37: the full round trip — write Berlin, read Berlin, and
        the moment the parsed cell is assigned to a LexModel DateTimeField
        the aware-on-assignment invariant restores the exact original instant.
        Given: the file written in 13.36, parsed with pd.read_excel
        When: a naive Berlin cell is assigned to FastExportItem.happened_at
        Then: the attribute is aware and denotes the original 09:00Z instant
        """
        df = pd.DataFrame({
            "name": ["summer"],
            "happened_at": pd.to_datetime([SUMMER_INSTANT], utc=True),
        })
        cell = pd.to_datetime(self._write_and_read_back(df)["happened_at"].iloc[0])
        self.assertIsNone(cell.tzinfo, "the parsed cell arrives naive — as Excel data does.")

        item = FastExportItem(happened_at=cell)
        self.assertIsNotNone(
            item.happened_at.tzinfo,
            "assignment must make the Excel-parsed value aware immediately.",
        )
        self.assertEqual(
            item.happened_at.astimezone(dt_timezone.utc),
            SUMMER_INSTANT,
            f"write→read→assign must reproduce the original instant; got "
            f"{item.happened_at} (≠ 09:00Z).",
        )
