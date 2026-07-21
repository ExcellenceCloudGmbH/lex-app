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

## Batch 13f — `_to_excel_naive` timezone stripping (2026-07-21)

| Field | Value |
|-------|-------|
| **Scenarios** | 13.31 – 13.36 |
| **Type** | U (13.31–13.35) + E (13.36) |
| **Files covered** | `lex/api/views/file_operations/ModelExport.py` |
| **Test file** | `lex/test_project/tests/exports/test_13f_tz_naive_excel.py` |
| **Test classes** | `TestCluster13f_ToExcelNaiveUnit`, `TestCluster13f_LegacyExportWithAwareDatetime` |
| **Fixtures** | none (unit tests use in-memory DataFrames; E2E uses `ExportItem`) |
| **Status** | complete — 6 pass / 0 fail |

**Context:** PR #661 (`fix/timezone-aware-utc`) added `_to_excel_naive` to
`ModelExport.py`. Under `USE_TZ=True` the ORM yields timezone-aware datetimes;
`xlsxwriter` raises `ValueError: Excel does not support datetimes with timezones`
unless tzinfo is stripped first. The fix converts aware datetimes to the display
timezone wall-clock and strips tzinfo before `to_excel`.

**Scenarios:**

- **13.31** — empty DataFrame → no-op (no columns to iterate)
- **13.32** — int/float/str columns → all values pass through unchanged
- **13.33** — `object` column with naive datetime → value unchanged (`_strip` returns it as-is)
- **13.34** — `object` column with UTC-aware Python datetime → converted to Berlin wall-clock, tzinfo stripped (summer: 10:00 UTC → 12:00 CEST)
- **13.35** — `DatetimeTZDtype` pandas column → `dt.tz_convert().dt.tz_localize(None)` path; winter + summer both correct
- **13.36** — E2E: legacy export POST with `filter_and_mask_data_for_export` patched to return a tz-aware DataFrame → HTTP 200, readable xlsx (the xlsxwriter ValueError regression does not fire)
