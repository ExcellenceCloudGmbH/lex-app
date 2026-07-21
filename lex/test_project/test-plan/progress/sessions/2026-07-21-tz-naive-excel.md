---
date: 2026-07-21
clusters: [13]
tests_added: 6
suite_tally: "exports: 6 pass / 0 fail (batch 13f only)"
---

# Coverage tests for PR #661 — `_to_excel_naive` in ModelExport

Closes coverage-task issue #664. Adds batch **13f** to cluster 13 (Export
Endpoint), covering the `_to_excel_naive` helper introduced in
`fix/timezone-aware-utc` (PR #661).

## Batches

- **Batch 13f** — 6 tests (5 pure-unit `SimpleTestCase` + 1 E2E
  `E2ETestCase`) for `_to_excel_naive` in
  `lex/api/views/file_operations/ModelExport.py`. See
  [clusters/13-exports/batches.md](../../clusters/13-exports/batches.md).

### What the batch covers

`_to_excel_naive` converts any timezone-aware datetime in the pandas DataFrame
to a naive wall-clock in `settings.TIME_ZONE` before `xlsxwriter` writes the
xlsx file. Without the function, `xlsxwriter` raises
`ValueError: Excel does not support datetimes with timezones` whenever
`USE_TZ=True` is in effect.

Scenarios 13.31–13.35 exercise every distinct branch of the function as pure
unit tests (no DB, no HTTP). Scenario 13.36 drives the full legacy
`POST /api/<model>/export` endpoint with `filter_and_mask_data_for_export`
patched to return a tz-aware DataFrame, confirming the endpoint returns HTTP 200
and a readable xlsx rather than a 500 error.
