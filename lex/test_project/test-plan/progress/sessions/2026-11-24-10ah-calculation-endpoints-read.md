---
date: 2026-11-24
clusters: [10]
tests_added: "4 new scenarios (10.198-10.201), batch 10ah"
suite_tally: "api_layer + 4p + 4m + 7m: 178 pass / 0 fail; on untouched lex-app-v2 10.198-10.200 fail on the leak and 10.201 passes"
---

# Batch 10ah: a calculation's live log and the running-calculations list follow permission_read

[Batch 10ah](../../clusters/10-api_layer/batches.md). Two calculation endpoints answered every
signed-in user without asking whether the user may read the record the calculation runs on.
`api/init-calculation-logs` returned the lines a run had logged so far, the backfill of the log
panel. `api/active-calculations` returned every running calculation with its record's display
name, model, primary key and calculation id, which is everything the first endpoint needs.

The calculation-log tree and its PDFs already had the rule (4.103-4.104, `LEX_SEC_INTERNAL`):
logs of records the caller may read, everything for a superuser. The two endpoints now share it
through `lex.api.security.calculation_record_readable` (the record a run is known by,
`<model_name>_<pk>`, judged by `is_readable`) and `sees_every_calculation`. A refused log answers
`{"logs": null}`, which is what a run with no log already answered, so the frontend's log panel
needs no change. The Instance Controller's release warning calls `api/active-calculations` with
the instance's own key, and that key keeps the full list.

The WebSocket side carries the same data (the status socket's reconnect snapshot, the live-log
socket's group) and still checks only the sign-in. That is cluster 9's surface and is left for
its own change.
