---
date: 2026-09-16
clusters: [5]
tests_added: 20
suite_tally: "history cluster: 61 pass / 0 fail / 1 skip / 1 xfail (5o: 20 pass / 0 fail)"
---

# Batch 5o — the database applies scheduled changes

Twenty scenarios for the in-database activation applier, allocated as
[batch 5o](../../clusters/05-history/batches.md) (5.110–5.129). The design is in
`docs/superpowers/specs/2026-09-16-bitemporal-activation-applier-design.md`; the batch
block carries the scenario map. 5n / 5.104–5.109 are reserved for PR #695's reconcile
floor, which is in flight on another branch.

Two things the tests taught that changed production code in the same change:

* The legacy local-timer branch of `_schedule_future_activation` reused its unique
  `meta_task_name` when a future row was re-chained by a later save within the same
  second — a `UniqueViolation` the handler swallowed into the log. It now carries the
  same uuid suffix the Celery branch always had.
* The applier's heartbeat is deliberately not a Django model (the framework registers
  every concrete model it finds under `lex/` for history tracking and the model list),
  which also means the test flush never removes it and a `--keepdb` database carries it
  across runs. A fresh heartbeat switches future-dated saves from "arm a timer" to
  "leave it to the database" — exactly as designed — so batch 5l's Celery-path
  assertions failed on a heartbeat from the previous run. `E2ETestCase` now clears it
  in `setUp` and `tearDown`.
