# Cluster 13 — Export Endpoint · Batch History

> Batch/allocation history for this cluster. Scenario intent lives in
> [`cluster.md`](cluster.md); machine allocation state in
> [`allocation.yaml`](allocation.yaml).

The Export Endpoint batches (13a legacy path, 13b AG-Grid flat, 13c AG-Grid
grouped/selected, 13d auth & edge cases, 13e streaming-cap) were authored in the
cluster definition rather than the retired `test-writing-plan.md`; see
[`cluster.md`](cluster.md) for their scenario-level definitions and
[`allocation.yaml`](allocation.yaml) for the allocation record. No additional
writing-plan batch blocks were recorded for this cluster.

> **Migration note (2026-07-07):** the retired `test-writing-plan.md` carried a
> `## Cluster 13 — Process Admin` block that reused the number 13 for an unrelated,
> never-opened area. It has been moved to
> [`../README.md` §6a](../README.md) as a pending decision — it does not belong here.

---

### Batch 13f — Export datetimes in the requester's browser timezone ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.31 – 13.33 |
| Type | U |
| Files covered | `lex/api/views/file_operations/ModelExport.py` (`_resolve_export_zone`, `_to_excel_naive(zone)`, `ModelExportView._normalize_cell_value` zone rendering, `post` `timezone` param) |
| Test file | `lex/test_project/tests/exports/test_13f_export_timezone.py` |
| Test classes | `TestCluster13f_ExportTimezone` (13.31 Berlin → 11:00; 13.32 New York → 05:00; 13.33 no/invalid tz → settings.TIME_ZONE) both on the legacy pandas path and the streaming/fast path |
| Fixtures | none — pure-function unit test (avoids the export permission-masking confound) |
| Tests landed | **3 pass / 0 fail** |
| Coverage gain | timezone-aware Excel export on both write paths |
| Status | ✅ Complete — Excel has no tz type, so aware-UTC datetimes are baked as a local wall-clock at export time, in the requester's browser zone (frontend sends `timezone` on the export request), falling back to `settings.TIME_ZONE`. Fixes exports showing naive UTC. Ships with the `USE_TZ=True` cutover. |

---

### Batch 13g — Report-file Excel boundary: display-zone wall-clock, both ways ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.34 – 13.37 |
| Type | U |
| Files covered | `lex/core/fields/XLSX_field.py` (`_excel_display_naive`, `XLSXField.create_excel_file_from_dfs` normalization hook) |
| Test file | `lex/test_project/tests/exports/test_13g_xlsx_field_timezone.py` |
| Test classes | `TestCluster13g_XlsxFieldTimezone` (13.34 aware column → Berlin wall-clock naive, incl. winter quarter-end landing on the right calendar day; 13.35 object columns / DatetimeIndex / MultiIndex levels / column headers all normalize, caller's frame untouched; 13.36 end-to-end through `create_excel_file_from_dfs` — the written .xlsx cells read Berlin wall-clock via `pd.read_excel`; 13.37 full round trip — a cell parsed back from the file, assigned to a LexModel `DateTimeField`, becomes aware and denotes the exact original instant) |
| Fixtures | none — in-memory workbook; storage stubbed at the `FieldFile.save` boundary; reuses `FastExportItem.happened_at` for the read-back assignment |
| Tests landed | **4 pass / 0 fail** |
| Coverage gain | the report-file (XLSXField) twin of the 13f export-endpoint timezone rendering |
| Status | ✅ Complete — `to_excel` raises on the aware datetimes every DateTimeField carries under USE_TZ=True, which crashed every report writing through `XLSXField` ("Excel does not support datetimes with timezones"). The boundary now renders aware values as the project display zone's wall clock (settings.TIME_ZONE) and strips tzinfo — the one place where naive is *correct*, because a spreadsheet cell is a wall-clock reading, not an instant. Reading mirrors it: parsed cells are naive local and the 3g assignment invariant restores the instant. |

---
