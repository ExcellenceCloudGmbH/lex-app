# Session — Recovery on demand: dispatch claims + in-flight mirror, consolidated

**Date:** 2026-07-27
**Batches:** 8z (8.145–8.156) and 8d (8.157–8.164)

## Why one change

8z (dispatch-time claims) and 8d (in-flight LIST mirror) were developed as
separate branches, and 8d's own notes recorded an unimplemented dependency
between them: *"if the dispatch-claim batch merges, `claim_dispatched()` must
gain the same SADD-guarded LPUSH."* Landing them apart would have shipped a
scale signal that ignores dispatched-but-unstarted work — which is precisely
the state the 1410 incident turned on. They are one change.

## What the combination buys

`claim_dispatched()` now mirrors onto the in-flight LIST under the same
SADD guard as `register()`. The consequence is behavioural: the KEDA scale
signal rises at **dispatch** rather than at task start, so the recovery
supervisor is up while the worker pod is still `Pending`. Under 8d alone the
supervisor would have stayed at zero for exactly that window.

## Correctness work beyond the two branches

- `reconcile_inflight_list()` converges entry-by-entry instead of `DEL` +
  rebuild. The rebuild reads the SET, clears the LIST, then rewrites it; an id
  a worker SADDs after that read but LPUSHes before the clear was wiped and
  never restored. The list would undercount and KEDA could scale the supervisor
  away mid-calculation. Removals re-check `SISMEMBER` against live state, so the
  residual race biases toward over-counting — which only keeps the pod up.
- `run_forever()` reconciles after every sweep, not just at startup. An
  on-demand pod outlives many calculations; startup-only reconciliation lets
  drift pin it up (or scale it away) until something restarts it.
- 8.147 updated: it asserted on `client.set`, but 8d moved `register()`'s
  payload write onto a pipeline. Behaviour unchanged, call surface moved — the
  assertion now reads whichever surface carried the write.

## Scenarios added

| Scenario | Pins |
| --- | --- |
| 8.161 | reconcile never drops an id registered mid-pass |
| 8.162 | duplicates collapse so `LLEN` is an exact count |
| 8.163 | the loop reconciles after every sweep, not only at boot |
| 8.164 | the signal rises at dispatch, and the claim→running upgrade does not double-count |

## Verification

- 8d: 8 pass. 8z: 12 pass.
- Regression `celery_async` + `calculations`: no failures beyond the documented
  pre-existing baseline. `test_7m_calc_signals` 7.192/7.193 fail identically on
  `origin/lex-app-v2` in the same combined run (ordering-dependent, unrelated).
- Infra companion: LEX_TERRAFORM_MODULES #35.
