---
date: 2026-10-06
clusters: [2, 6, 7, 15]
tests_added: 11
suite_tally: "2k 2 pass, 6r 2 pass, 7t 6 pass, 15k 1 pass (11 pass / 0 fail); full cluster suite 1650 pass / 38 fail / 27 skip / 6 xfail / 5 collection errors - every failure reproduces on lex-app-v2 under the same conditions (no reflex package locally, cluster-15 rows when run after cluster 6, stress timing, gate self-test)"
---

# Batch 7t — a record created through the app can start its own calculation

Came in as customer feedback: trigger a record's calculation in the background right
after creating it, acting the same as the Calculate button. A model now sets
`calculate_on_create = True`, and `OneModelEntry.create` starts the run once the create
has committed, through the button's own start code, which moved out of `update` into
two methods both paths call.

Four batches, one per surface:
[7t](../../clusters/07-calculations/batches.md) — the run and when it starts;
[2k](../../clusters/02-crud_api/batches.md) — the POST answers 201 IN_PROGRESS without
waiting; [6r](../../clusters/06-audit_logging/batches.md) — the run's own audit entry;
[15k](../../clusters/15-calculation_logging/batches.md) — the row finds the run's log.

All 11 were written first and failed for the right reason. Two deliberate breakages
showed they catch a wrong implementation, not only a missing one: the run sharing the
create's audit entry (6r fails), and the run filed under the create request's own id,
which has no pk (15k and 6r fail). The button's own tests (6i, 2i) pass unchanged.

**A flake fixed on the way.** 7m (7.192/7.193) failed about one run in five on
lex-app-v2 itself: it patches `One._calculation_executor` to keep the Calculate
button's background run from starting, but `One.py` imported the executor inside
the method, so the patch never applied and the run raced the test's table flush
("deadlock detected"). `One.py` now imports the executor at module level, the patch
takes effect, and 7m passed 10 runs out of 10.
