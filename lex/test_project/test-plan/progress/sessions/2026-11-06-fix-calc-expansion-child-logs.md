---
date: 2026-11-06
clusters: [15]
tests_added: "9 new scenarios (15.56-15.64, batches 15m and 15n); measured counts recorded for 15a-15f; legacy test_create_treats_empty_selections_as_valid_noop corrected"
suite_tally: "calculation_logging 63 pass / 0 fail / 3 xfail (was 45 / 9 / 3 on v2.4.2, before 15m-15n); audit_logging 153 pass / 1 xfail; calculations 218 pass; celery_async 149 pass / 7 skip; signals_ws 49 pass; api_layer 110 pass; lex/tests/unit/calculation 11 pass (was 10 / 1 fail)"
---

# Batches 15m, 15n — a generated row's log is filed under that row again

Both defects turned up while the "long-lived secure sessions" suite runs were compared with
v2.4.2. Neither is related to sign-in, and v2.4.2 has both.

**BUG-052: child logs filed under the root.** `eec74b6d` (2026-05-14) replaced
`model_logging_context(model)` with `calculation_execution_context()` in `calc_and_save_sync` and in
Celery's `calc_and_save`, when the two were meant to be nested. `calc_and_save_streaming`
(2026-06-10, now the default sync route) copied the result. From then on every row a `create()`
generated had its lines appended to the root's log. 15b–15e had pinned the nesting two days earlier.
They pass on `eec74b6d~1` (18 pass / 3 xfail) and fail on `eec74b6d` (the same nine as today). They went
unnoticed because no CI job ran `calculation_logging`: the 2026-10-22 note recorded them as
"cross-test interference", but they are this bug. All three consumers now go through one helper,
`CalculationModel.child_calculation_context`.

**BUG-053: lines written before their model had a node.** Restoring the push alone would have
brought back the behaviour from before May. A new row has no pk while it calculates, and outside a
transaction (every Celery worker, `is_atomic = False` roots) its lines are written at once. They were
looked up by `object_id = NULL`, so all new siblings shared one node with no record; a probe
reproduced exactly that. `CalculationLog` now resolves rows from the frame stack:

- an unsaved model's first line creates a node of its own, kept on the instance and linked to the
  record when its block ends;
- a parent's first row is created under its own parent, not detached;
- `copy_for_dispatch` creates the nodes of unsaved models before a Celery task gets its copy of the
  stack.

A root running in the generic `calc_and_save` task is not pushed a second time on top of its own
shipped copy.

[15m](../../clusters/15-calculation_logging/batches.md) covers the synchronous routes and
[15n](../../clusters/15-calculation_logging/batches.md) the Celery route (eager). Each scenario was
checked to fail with its part of the fix undone. 15f stays xfail: a `CalculationModel` saved inside
another calculation still does not push itself, which is a separate fix in `execute_calculation_sync`.

**Empty selections (no BUG row: the test was wrong, not the code).**
`lex/tests/unit/calculation/test_calculated_model_mixin.py::test_create_treats_empty_selections_as_valid_noop`
expected `_dispatch_model_processing` to be called with `{}`. Since `7efe7b1f` (2026-06-10) the
default sync route streams and returns before the four-step pipeline, as
`docs/reference/CalculatedModelMixin Internals.md` documents. The file had been deleted on 06-05 and
was restored on 06-29 already failing; the 7q note recorded it as a pre-existing streaming-path
failure. The intended behaviour, an empty selection is a no-op that calculates nothing, still holds.
The test now asserts that on both routes, with the dispatch kept to `LEX_SYNC_STREAMING_EXPANSION=false`.

**CI.** `calculation_logging` is registered in `.github/scripts/showcase_clusters.py` and added to the
release-gate selector in `showcase_tests.yml`, `pip_publish.yml` and `prerelease_gate.yml`.

**Whole-suite runs (BUG-030's calculation_logging group).** A whole-suite run still failed 32 of the
cluster's tests on both v2.4.2 and this branch. The cause was a leak, not product code:
`test_6b_calculation_audit.py` calls `ensure_terminal_calculation_audit` directly, which leaves a
reference to its AuditLog in the runner thread's context. Cluster 15's seed copied that reference after
the flush had deleted the row, and every log write then failed. `audit_logging` followed by
`calculation_logging` reproduces all 32 failures. Two fixes: 6b now isolates its context per test, and
cluster 15 seeds a fresh context. Each one alone gives 216 pass / 4 xfail for the pair.

Both whole-suite runs also hung on the same test, `test_1aq_reflex_dashboards.py::test_1_360`
(`run_orm` across the event loop), after 1197 results on v2.4.2 and 1206 here (the 9 new tests). That
file is part of the open nest_asyncio / async_to_sync deadlock fix (PR 865), so the hang is unrelated
to this change.

**Follow-up for `lex-app-docs`** (the `calculations` and `reference` docs are mirrored read-only
here). Logging.md could say that rows a `create()` generates are nested automatically, on every
route, including new rows whose log is linked once they are saved. The Internals page's "In both
paths" should become "in every path (streaming, materialising, Celery)".
