---
date: 2026-10-22
clusters: [6, 7]
tests_added: "29 new scenarios (6.168, 7.238-7.265)"
suite_tally: "test_7v_calculation_finished_events.py + test_6u_fleet_metrics_audit_contract.py: 31 pass / 0 fail, 4 subtests pass — confirmed deterministic across repeated full-file runs and every test run alone"
---

# Batches 6u, 7v — the calculation_finished fleet-metrics event

Branch `feat/fleet-metrics-calculation-events`, continuing from the event emitter and heartbeat work
on `feat/fleet-metrics-emitters`. Scope: lex-app sends one `calculation_finished` event per root
calculation run, through that emitter. See [7v](../../clusters/07-calculations/batches.md) and
[6u](../../clusters/06-audit_logging/batches.md) for what each batch covers — not restated here.

**Pass 1:** found and recorded BUG-034 (the startup-abort sweep never produced an audit row or
event) rather than weaken the test pinning it; see `known-bugs.md`.

**Pass 2, same day:** fixed BUG-034 on this branch. Along the way: 7.261 wasn't actually isolating
the root-run guard it claimed to test (fixed, RED/GREEN confirmed with the guard temporarily
removed); the pending-row lookup could have mistaken an older, superseded run's entry for the
current one's (fixed — only the *most recent* `AuditLog` per object counts, 7.262 pins it); the emit
hook's import-failure handler logged at DEBUG, invisible in production (raised to a throttled
WARNING).

**Pass 3, same day:** the pass-2 lookup's two queries still sat outside the per-row `try/except`,
so their own failure (a DB error, a non-integer pk against the `object_id`
`PositiveIntegerField`) escaped and stopped the whole startup sweep — a crash loop on every
restart. Extracted into `ModelRegistration._resolve_pending_audit_logs`, now wrapped in its own
`try/except` (WARNING + fall back to "no pending row", same as before this lookup existed),
non-integer pks filtered out before any query, and the query itself changed to an aggregate
(`Max("id")` per `object_id`) that never loads `payload`. RED/GREEN confirmed by reverting to the
pass-2 code and watching the new failing-prefetch test fail with an uncaught `RuntimeError`, then
restoring the fix. The previously-unused `_reset_emit_hook_warning_throttle_for_tests` now has a
real test (`TestCluster07v_EmitHookWarningThrottle`, 7.265).
