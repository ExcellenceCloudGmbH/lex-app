---
date: 2026-07-15
clusters: [8z]
tests_added: "12 (8.145–8.156) + source in 5 files; deliberate 8x _run_sweep edit (age-gate off)"
suite_tally: "8z 12 pass / 0 fail; regression: celery_async+init+calculations = 606 pass / 20 skip / 0 fail"
---

**Batch 8z landed — recovery hardening from the 2026-07-14 instance-1410
incident.** Root cause chain (verified in cluster logs + code): a ~20-way
fan-out hit a full cluster, most worker Jobs sat Pending; the autoscaler
independently evicted the node hosting the instance's redis + recovery-beat;
the rescheduled recovery-beat's Django startup ran the blind startup reset,
which aborted a healthy, merely-queued calculation — because registry
ownership only began at `task_prerun`, so a dispatched-but-unstarted task was
invisible. Meanwhile the idle watchdog (armed on `worker_ready`) could not
reap workers that booted into the dead broker, and flushing redis as
remediation destroyed queue and recovery tracking together.

Hardening: (1) ownership starts at dispatch — `CallbackTask.apply_async` is
the single dispatch choke point and claims the task NX with
`status="dispatched"`/`claimed_at` and no heartbeat; (2) the supervisor's
dispatched lane uses the broker queue itself as the liveness signal — waiting
claims are skipped BEFORE the recovery lock, and only a verifiably vanished
message (flush/eviction) is requeued with the same task id, making double
dispatch impossible by construction; (3) the startup reset spares young
untracked rows (`LEX_STARTUP_ABORT_MIN_AGE_SECONDS`, default 1800, 0=legacy)
so registry-unreadable degradation can't race a scheduling backlog; (4) a
boot watchdog armed on `worker_init` (`LEX_WORKER_BOOT_TIMEOUT_SECONDS`,
default 300, 0=off) terminates workers that never become ready. Design
respects the founding PRs (#596/#603/#605): visibility_timeout untouched,
best-effort everywhere, budget-split around dispatch, never resurrect settled
work. Infra companions (redis safe-to-evict annotation, ScaledJob
activeDeadlineSeconds) tracked separately in LEX_TERRAFORM_MODULES.
See [batch 8z](../../clusters/08-celery_async/batches.md).
