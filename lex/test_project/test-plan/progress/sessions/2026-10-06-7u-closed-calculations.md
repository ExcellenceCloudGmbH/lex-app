---
date: 2026-10-06
clusters: [2, 7, 12]
tests_added: 18
suite_tally: "7u 10 pass, 2l 3 pass, 12m 5 pass (18 pass / 0 fail); full cluster suite 1668 pass / 38 fail / 27 skip / 6 xfail / 5 collection errors - 37 failures are the same as on the calculate-on-create branch (no reflex package locally, cluster-15 rows when run after cluster 6, stress timing, gate self-test); the 38th, 8.89, came from this change and passes after its mock was given an answer"
---

# Batch 7u — a closed record is never calculated again

Came in as customer feedback: built-in support for the `is_closed` / `sap_posted` pattern, where a
calculation whose result has been used must not run again, keeps its status, and tells the user
why, including when calculations call calculations. A model now answers
`calculation_closed_reason()`, and every path that starts a calculation asks it first.

Three batches, one per surface:
[7u](../../clusters/07-calculations/batches.md) — the calculation paths themselves;
[2l](../../clusters/02-crud_api/batches.md) — the Calculate button's 409;
[12m](../../clusters/12-serializers/batches.md) — the greyed button and the reason on every row.

Before the change, the closed-record tests failed for the right reason and the open-record
controls passed. Each guard was then broken on purpose, one at a time, and only its own tests
failed: the status restore (7.228, 7.229, 7.235), the skip log (7.231), the scopes discard
(12.58), the 409 (2.110, 2.112), the three batch skips (7.232, 7.233, 7.234), the create check
(7.236).

**Found on the way.** A greyed Calculate button said "You do not have permission to calculate
this record", whatever the cause. The rows now carry the reason, and the frontend shows it
(F7j in the frontend plan). And a model closing itself on `is_calculated == SUCCESS` was refused by
the button but recalculated by a nested save, which saw the IN_PROGRESS it was asking for: the save
now asks with the status from before it (7.237).

**One existing test adjusted.** 8.89 hands `calc_and_save` a bare `MagicMock` as its model; a
MagicMock answers `calculation_closed_reason()` with a truthy mock, which reads as closed, so the
task skipped it. The mock now answers `None`. A truthy answer from a real model still closes it.

`lex/core/calculated_updates` also calls `calculate()` directly, but nothing instantiates its
handler in v2, so it cannot start a calculation; it was left alone.
