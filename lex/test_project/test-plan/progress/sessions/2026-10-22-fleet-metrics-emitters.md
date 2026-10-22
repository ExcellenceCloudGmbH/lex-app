---
date: 2026-10-22
clusters: [1]
tests_added: "22 new scenarios (1.455-1.474, 1.475-1.476)"
suite_tally: "init (minus 4 pre-existing collection errors on an optional reflex/reflex_enterprise package not installed in this venv, and 6 pre-existing lex-reflex-CLI failures from the same gap): 739 pass / 0 fail / 14 skip / 174 subtests pass"
---

# Batches 1ba, 1bb — fleet-metrics push build, Task 11, PR F part 1 (emitter, heartbeat)

Branch `feat/fleet-metrics-emitters`, from `origin/lex-app-v2`. Scope: the event emitter and the
hourly heartbeat -- half of lex-app's fleet-metrics phase-1 push build (source-plan.md section
"lexapp"). Split out to keep each branch under the 1,500-changed-line review budget; the usage
hash and the usage payload are `feat/fleet-metrics-usage-payload`, merged in underneath this
branch. The calculation emitter, the finaliser hook, the Celery flush and the 6u audit-contract
test are Task 12, on their own branch.

[1ba](../../clusters/01-init/batches.md) covers `lex/lex_app/fleet_metrics/emitter.py`: the
`enabled()` gate (the same three variables `_is_deployed_instance` used in the removed version
reporter, commit 0db88ad9), with each of the three variables pinned as independently required
(1.456); the bounded 1,000-event queue that drops the oldest; batches capped at 100; the
one-retry-then-drop-with-a-throttled-WARNING delivery contract; the single daemon drain thread;
`flush(timeout)`; and a forked child process resetting its queue, locks and drain-thread state
via `os.register_at_fork` (1.475-1.476), load-bearing for task 12's Celery-worker emitters.
`note_failure()`'s per-label WARNING throttle uses `None`, never `0.0`, as its "never warned"
sentinel, because `time.monotonic()`'s reference point is unspecified and can legitimately be
small early in a process's life -- a `0.0` sentinel would let the very first warning for a label
be mistaken for one that just fired. `flush(timeout)` checks its deadline before dequeuing the
next batch, not after, so an unsent batch is never pulled off the queue and then abandoned once
the budget runs out.

[1bb](../../clusters/01-init/batches.md) covers `lex/lex_app/fleet_metrics/heartbeat.py`:
web-process-only scheduling via `running_in_uvicorn()`, the immediate first beat, the payload
shape, and `usage_enabled` following the switch both ways.

`guard()` and `note_failure()` in `emitter.py` implement the swallow-and-throttle contract
source-plan.md attributes to lex-mcp-local's `src/lex_mcp/analytics.py:165-247` directly, rather
than copying that file line for line.
