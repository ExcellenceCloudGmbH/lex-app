---
date: 2026-10-22
clusters: [1]
tests_added: "26 new scenarios (1.455-1.480)"
suite_tally: "init 1ba + 1bb: 26 pass / 0 fail"
---

# Batches 1ba, 1bb — the fleet-metrics event emitter and heartbeat

Branch `feat/fleet-metrics-emitters`, on top of `feat/fleet-metrics-usage-payload`: the emitter
that posts fleet-metrics events to the Instance Controller, and the hourly heartbeat. The
calculation events that use this emitter follow on `feat/fleet-metrics-calculation-events`.

[1ba](../../clusters/01-init/batches.md) covers `lex/lex_app/fleet_metrics/emitter.py`: the
`enabled()` gate (the same three variables `_is_deployed_instance` used in the removed version
reporter, commit 0db88ad9), each pinned as independently required (1.456); the bounded
1,000-event queue that drops the oldest; batches capped at 100; one retry and then a drop with a
throttled WARNING; each 30 s tick posting until the queue is empty and stopping at the first
dropped batch (1.477-1.478); only a 2xx counting as delivered, with redirects never followed
(1.479-1.480); the single daemon drain thread; `flush(timeout)`; and a forked child process
resetting its queue, locks and drain-thread state via `os.register_at_fork` (1.475-1.476).

[1bb](../../clusters/01-init/batches.md) covers `lex/lex_app/fleet_metrics/heartbeat.py`:
web-process-only scheduling via `running_in_uvicorn()`, the immediate first beat, the payload
shape, and `usage_enabled` following the switch both ways.
