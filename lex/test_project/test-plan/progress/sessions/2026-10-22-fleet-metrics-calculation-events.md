---
date: 2026-10-22
clusters: [6, 7]
tests_added: "32 new scenarios (6.168, 7.238-7.268)"
suite_tally: "audit_logging + calculations + celery_async, with 1ba, 1bb and 10s: 595 pass / 0 fail / 7 skip / 1 xfail, 95 subtests pass"
---

# Batches 6u, 7v — the calculation_finished fleet-metrics event

Branch `feat/fleet-metrics-calculation-events`, on top of `feat/fleet-metrics-emitters`: lex-app
sends one `calculation_finished` event per root calculation run through that emitter. See
[7v](../../clusters/07-calculations/batches.md) and [6u](../../clusters/06-audit_logging/batches.md)
for what each batch covers — not restated here.

7v also fixes BUG-034 (see `known-bugs.md`): the startup-abort sweep finalises the interrupted
run's own pending `AuditLog`, found by a bounded, time-limited lookup that falls back to the old
behaviour on any failure.
