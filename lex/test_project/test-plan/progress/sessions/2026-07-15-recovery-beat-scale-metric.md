---
date: 2026-07-15
clusters: [8c]
tests_added: "3 (8.157–8.159)"
suite_tally: "8c 3 pass / 0 fail"
---

**Batch 8c landed — the lex-app half of making the recovery-beat pod on-demand.**
Today the recovery-beat pod (embedded beat firing the dead-worker sweep) runs
always-on, but it only has work while calculations are in flight. This adds the
scale signal KEDA needs to run it on demand: `GET api/recovery-scale-metric`
returns `{"count": N}` where `N = max(recovery-registry cardinality,
active-calculation store size)` — the cross-process Redis registry unioned with
the in-process DB-reconciled store, so neither alone can scale the sweeper off
live work, and any error fails safe upward. KEDA keeps the pod at one replica
while `count > 0` and scales to zero when idle.

Infra companion (LEX_TERRAFORM_MODULES, drafted for Marco's review): convert
`celery_beat_recovery.yaml` from an always-on Deployment to a KEDA ScaledObject
(min 0 / max 1) with a `metrics-api` trigger on this endpoint. **Open sequencing
decision:** the recovery-beat also drives bitemporal future-activation clocked
schedules — those must move to the planned global scheduler (Marco + team, end of
next week) before or alongside this change, or future activations won't fire
while the pod is at zero replicas. Scenarios start at 8.157 to avoid colliding
with the unmerged dispatch-claim batch (8z, 8.145–156, PR #653).
See [batch 8c](../../clusters/08-celery_async/batches.md).
