---
date: 2026-07-20
clusters: [1, 12, 5, 13]
tests_added: 6
suite_tally: "init+serializers+history: 358 pass / 0 fail / 14 skip / 2 xfail; exports 21 pass"
---

# Timezone-aware UTC cutover + incident data migration

Adopted the aware-UTC datetime convention framework-wide (`USE_TZ=True`,
`TIME_ZONE="UTC"`; removed the BUG-025 `DATETIME_FORMAT` hack) and shipped the
`rebase_incident_datetimes` command to correct the data corrupted by the
TIME_ZONE incident. Full rationale + proof in the ADR
[`docs/releases/tz-decision-usetz-true.md`](../../../../../docs/releases/tz-decision-usetz-true.md);
the incident diagnosis is **BUG-027** in [`known-bugs.md`](../../known-bugs.md).

## Batches

- **Batch 1i** — the `rebase_incident_datetimes` maintenance command (new source
  `lex/lex_app/management/commands/rebase_incident_datetimes.py`). See
  [clusters/01-init/batches.md](../../clusters/01-init/batches.md). 3 pass.
- **Batch 12j** — datetime write→read round trip under the aware-UTC convention.
  See [clusters/12-serializers/batches.md](../../clusters/12-serializers/batches.md).
  3 pass. Complements 12g (BUG-025 designator gate, still green).

## Framework changes and their blast radius (measured, baseline-diffed)

The `USE_TZ=True` flip's deterministic effect on the existing suite was small and
fully addressed:

- **Cluster 5m (as_of)** — 2 tests (`test_5_98`, `test_5_103`) encoded the retired
  naive-UTC convention (a helper that stripped tzinfo; a "naive" anchor that was
  no longer naive once `lex_datetime_now()` became aware). Updated to the aware
  convention; behavior is now *more* correct. No new scenarios.
- **Cluster 13 (exports)** — real bug: under aware datetimes `xlsxwriter` raises
  *"Excel does not support datetimes with timezones"*. Fixed in
  `lex/api/views/file_operations/ModelExport.py` (`_to_excel_naive` localizes
  aware datetimes to `settings.TIME_ZONE` and strips tzinfo before `to_excel`).
  Covered by the existing 13.2a/13.2b/13.3 gates (fail without the fix, pass with).
- **Cluster 15 (calculation_logging)** — **no framework regression**: in isolation
  the cluster fails exactly the 9 pre-existing/order-flaky tests, identical to
  baseline. The full-suite run shows additional cluster-15 failures, but those are
  pre-existing test-ordering leakage (the cluster is documented order-sensitive),
  reproducible independent of this change.

Core clusters green under the flip: **init + serializers + history = 358 pass**,
exports = 21 pass.
