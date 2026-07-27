---
date: 2026-07-15
clusters: [8d]
tests_added: "4 (8.157–8.160)"
suite_tally: "8d 4 pass / 0 fail; regression: full celery_async = 144 pass / 7 skip / 0 fail"
---

**Batch 8d landed — the lex-app half of scaling the recovery pod to zero
(design locked as Option A: parallel Redis list).** The recovery pod only has
work while the registry index SET is non-empty, but KEDA's native `redis`
scaler reads list length, not set cardinality — so the registry now maintains
`<id>:lex:recover:inflight` as an exact LIST mirror of the index: SADD-guarded
LPUSH in `register()` (requeue re-registers never double-count), LREM count-0
in `deregister()`, and `reconcile_inflight_list()` at supervisor startup for
mid-cutover/crash safety. Chosen over the earlier HTTP metrics-endpoint draft
(superseded, PR #654 closed): the native scaler reuses the worker ScaledJob's
existing TriggerAuthentication (no new auth wiring) and shares the work's
failure domain — a leaked entry keeps recovery up, wasteful not unsafe.
Behaviourally inert until the infra half points KEDA at the list; that half
(recovery pod as ScaledObject, min 0 / max 1) is drafted but **blocked** on
relocating the bitemporal future-activation clock to the planned global
scheduler. Interaction note: if the dispatch-claim batch (8z, PR #653) merges,
`claim_dispatched()` needs the same SADD-guarded LPUSH — one-line follow-up in
whichever lands second. See [batch 8d](../../clusters/08-celery_async/batches.md).
