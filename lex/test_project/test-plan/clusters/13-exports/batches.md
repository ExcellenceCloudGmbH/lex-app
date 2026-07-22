# Cluster 13 — Export Endpoint · Batch History

> Batch/allocation history for this cluster. Scenario intent lives in
> [`cluster.md`](cluster.md); machine allocation state in
> [`allocation.yaml`](allocation.yaml).

The Export Endpoint batches 13a–13e were authored in the cluster definition
rather than the retired `test-writing-plan.md`; see [`cluster.md`](cluster.md)
for their scenario-level definitions and [`allocation.yaml`](allocation.yaml)
for the allocation record. Newer follow-up batches are appended below.

> **Migration note (2026-07-07):** the retired `test-writing-plan.md` carried a
> `## Cluster 13 — Process Admin` block that reused the number 13 for an unrelated,
> never-opened area. It has been moved to
> [`../README.md` §6a](../README.md) as a pending decision — it does not belong here.

---

### Batch 13f — Pandas fallback aware-datetime export ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.31 – 13.31 |
| Type | E |
| Files covered | `lex/api/views/file_operations/ModelExport.py` (`_to_excel_naive`, pandas fallback branch in `post`) |
| Test file | `lex/test_project/tests/exports/test_13f_pandas_timezone_export.py` |
| Test classes | `TestCluster13f_PandasTimezoneExport` |
| Fixtures | reuse cluster-13 `FastExportItem`; patch both streaming fast paths to force the pandas fallback route |
| Tests landed | **1 pass / 0 fail** (`DATABASE_DEPLOYMENT_TARGET=local`) |
| Coverage gain | Pins the aware-datetime → local naive conversion the pandas fallback must apply before `DataFrame.to_excel` |
| Status | ✅ Complete — 1 pass / 0 fail (`DATABASE_DEPLOYMENT_TARGET=local`) |
| Note | Regression for PR #661 / the aware-UTC cutover: AG export can legitimately fall back from both streaming paths, and under `USE_TZ=True` the fallback DataFrame still carries aware ORM datetimes. Without `_to_excel_naive`, `xlsxwriter` raises `Excel does not support datetimes with timezones`; a naive `replace(tzinfo=None)` would export the wrong customer-visible wall-clock. This batch forces the real `post()` entry point through the fallback and asserts that a UTC instant is written as the active display-zone time (`Europe/Berlin` in the regression fixture), proving both the crash and silent time-shift are blocked. |
