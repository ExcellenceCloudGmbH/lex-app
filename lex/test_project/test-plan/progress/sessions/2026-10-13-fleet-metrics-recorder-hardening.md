---
date: 2026-10-13
clusters: [10, 6]
tests_added: 5
suite_tally: "test_10o_usage_recorder.py + test_6s_fleet_metrics_contract.py: 41 pass / 1 xfail (strict) / 0 fail (25 subtests, -v); 10o under DATABASE_DEPLOYMENT_TARGET=local: 8 pass / 28 skip / 0 error; test_6r_create_triggered_run_audit.py + test_11b_list.py (11.4): 3 pass; test-plan validate: 79 pre-existing problems, all cluster-1 duplicate-scenario findings, none in cluster 6 or 10"
---

# Batches 10o and 6s hardened: no time of day, anonymous requests write nothing, the switch fails closed

[Batch 10o](../../clusters/10-api_layer/batches.md) drops `first_seen` from
`lex_usage_daily` — a UTC day is all the collector reads, and a per-person,
per-area, per-day timestamp was never read by anything. A request with no actor
(an anonymous `/health` probe, among others) now writes neither table, including
the version row, and does not disturb its hourly throttle either — liveness stays
database-free. `LEX_USAGE_METRICS` now fails closed on a value that is neither a
recognised "on" nor a recognised "off", warning once per process rather than
silently defaulting on. New scenarios 10.115–10.118 cover the throttle and the
switch; `TestCluster10o_RecorderEndToEnd` and `TestCluster10o_UsageAreaEndpoint`
now skip on a non-PostgreSQL backend before their parent's `setUp` starts its
patches, instead of erroring on the missing tables.

[Batch 6s](../../clusters/06-audit_logging/batches.md) gains 6.126: an end-to-end
check of whether an API key's audit entries actually carry its name, the premise
K1's writer exclusion depends on. They do not — `AuditLogMixin.log_change` reads
`request.user`, which stays `AnonymousUser` for an API-key-only call — tracked as
BUG-034 in `known-bugs.md` and marked `xfail(strict=True)`.

`test_11b_list.py` (11.3) now makes one unmeasured warm-up request before its
query-count budget, so the recorder's own inserts land outside the measured
block regardless of test order.
