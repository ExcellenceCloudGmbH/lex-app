## Cluster 15 — Calculation Logging

(batch history lived under cluster 7 before the 2026-07-07 promotion — see ../07-calculations/batches.md for pre-promotion rows)

---

### Batch 15g — Object-less heading frames (TOC nodes) in the log tree ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.22 – 15.31 |
| Type | I |
| Files covered | `audit_logging/utils/ModelContext.py` (`LogHeading`, string acceptance, `get_root_model`/`get_current_model`), `audit_logging/utils/ContextResolver.py` (frames snapshot, routing skips headings), `audit_logging/models/CalculationLog.py` (`heading` field, lazy heading rows, `__str__`), `audit_logging/utils/DataModels.py` (`ContextInfo.frames`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15g_heading_context.py` |
| Test classes | `TestCluster15g_HeadingFrames`, `TestCluster15g_HeadingPersistence` |
| Fixtures | reuse cluster-15 `LogRootCalc` + `_seed_operation_context_and_audit_log` (via `_CalcLogTestCase`) |
| Est. tests | 10 |
| Coverage gain | measured with batch 6q: heading persistence paths in ModelContext/ContextResolver/CalculationLog |
| Prereqs | none |
| Status | ✅ Complete — 10 pass / 0 fail |
| Note | Feature batch for `feat/calclog-heading-context`: `model_logging_context("Section title")` pushes a `LogHeading` frame producing a table-of-contents style node — a `CalculationLog` row with `heading` set and `content_type`/`object_id` NULL, created lazily on the first LexLogger flush inside the block. Design: `docs/superpowers/specs/2026-07-07-calclog-heading-context-design.md`. Companion batch 6q covers root-detection transparency. |

---

### Batch 15h — PDF export renders like the log view ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.32 – 15.34 |
| Type | E |
| Files covered | `api/views/calculations/DownloadMarkdownPdf.py` |
| Test file | `lex/test_project/tests/calculation_logging/test_15h_pdf_export.py` |
| Test classes | `TestCluster15h_PdfExport` |
| Fixtures | plain `CalculationLog` rows (all fields defaulted) |
| Est. tests | 3 |
| Coverage gain | calc-log PDF export contract (render parity with the log view) |
| Prereqs | none (pypdf available in the test venv for text extraction) |
| Status | ✅ Complete — 3 pass / 0 fail |
| Note | Customer report 2026-07-14: the exported PDF "doesn't look like the log view — markdown is not being rendered correctly". Root causes: (1) xhtml2pdf's minimal CSS support + a bare-bones stylesheet, (2) the `code-friendly` markdown2 extra silently DISABLED `__bold__` (which the log view's markdown-it renders), and `~~strike~~` was never enabled. Fix: render via **WeasyPrint** (near-browser CSS fidelity; already in requirements.txt) with a GitHub-style stylesheet mirroring the frontend log view (bordered headings, shaded code blocks with pre-wrap, bordered tables with tinted header, blockquote rail, link styling, image support); markdown extras now `tables, fenced-code-blocks, strike`. xhtml2pdf kept as a runtime fallback (WeasyPrint needs pango/cairo system libs — the endpoint degrades in styling rather than 500ing). 15.32 real PDF (magic bytes + disposition); 15.33 raw markdown syntax (`###`, `|---`, `**`, `~~`, ```` ``` ````, `](https`) never leaks into the extracted text; 15.34 every log-view feature's content survives (headings, both bold forms, strike body, all table rows, quote, fenced + inline code, link text). Frontend twin: the Download button (`CalculationLogFieldView.downloadCalculationLogPdf`) needs no change — it downloads what this endpoint produces. |

## Batch 15i — a calculation row learns its latest run (15.39-15.46)

| | |
| --- | --- |
| Scenario range | 15.39 – 15.46 |
| Type | U + API |
| Files covered | `lex/audit_logging/utils/latest_calculation.py` (new), `lex/api/serializers/base_serializers.py` (`_calculation_run_fields`, `_SYSTEM_FIELDS`, both factories), `lex/api/views/model_entries/List.py` (`_execute_leaf_level`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15i_latest_run_on_the_row.py` |
| Tests landed | **10 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | PAC batches `7f` and `12m` — the status cell's log button and the drawer that read these two fields |

**The rule is stated twice, deliberately.** `useResolvedCalculationId` resolves a record's run in the
browser; this resolves a whole page on the server. Both use the newest `calculationId` starting
`<model>_<pk>_`, by `id`. A second rule — the spec's first draft proposed the generic foreign key —
would let one row open different runs in the table and in a widget.

**15.41 exists because the obvious implementation is wrong for string keys.** A run id can start
with two of a page's prefixes; only the longest is the record that started it. Verified as a guard:
matching shortest-first fails 15.41 and nothing else.

15.43's second half exists because the first implementation shadowed the audit log's own getter: _wrap_custom_serializer builds (LexSerializer, custom_cls), so a same-named method on the shared base came first in the MRO and unannotated audit rows answered False. The fields now name their getters explicitly.

## Batch 15j — a failed run keeps what it logged (15.47-15.50)

| | |
| --- | --- |
| Scenario range | 15.47 – 15.50 |
| Files covered | `lex/audit_logging/models/CalculationLog.py` (`keep_rolled_back_log`), `lex/core/models/CalculationModel.py` (both failure paths) |
| Test file | `lex/test_project/tests/calculation_logging/test_15j_failed_run_keeps_its_log.py` (4) |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |
| Paired with | process-admin-general-client F12 `12r` |

**The steps that succeeded vanished with the one that failed.** `CalculationLog` persists on commit, so a
calculation that fails inside its transaction — every `is_atomic` model — was rolled back with every row
it wrote. The log popup could show the failure's traceback but nothing that led up to it.

**The live cache still had it.** The cache is not transactional: it holds the run's whole log, in
order, under the root's key until the run ends. `CalculationLog.keep_rolled_back_log` writes that text
back as the run's log, one row on the root record, in both failure paths, just before the cache is
purged. It does nothing when the run's rows survived, and it never raises. 15.47 is the regression.
15.48 checks what the popup reads: the audit row's `lex_reserved_has_calculation_log` turns true.

### Batch 15k — The grid opens the log of a run a create started ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.51 |
| Type | E |
| Files covered | `lex/api/views/model_entries/One.py` (`_calculate_after_create`), read through `lex/audit_logging/utils/latest_calculation.py` by the list view |
| Test file | `lex/test_project/tests/calculation_logging/test_15k_create_triggered_run_log.py` |
| Test classes | `TestCluster15k_CreateTriggeredRunLog` — 15.51 after a create-started run, the list endpoint's row names it (`lex_reserved_calculation_id` starts `<model>_<pk>_`), says it has a log, and the log rows are stored under that id |
| Fixtures | `LogOnCreateCalc` — added to `calculation_logging/models.py`, kept out of `ALL_MODELS` |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete — paired with 7t. Checked to fail when the run is filed under the create request's own id (`<model>_create_<uuid>`, which has no pk) |

---

### Batch 15l — The calculation-log PDF download writes an audit entry ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.52 – 15.55 |
| Type | E |
| Files covered | `lex/api/views/calculations/DownloadMarkdownPdf.py` |
| Test file | `lex/test_project/tests/calculation_logging/test_15l_pdf_download_audit.py` |
| Test classes | `TestCluster15l_PdfDownloadAudit` (15.52 single-log PDF → entry against the log with filename and size, `include_descendants: false`; 15.53 subtree download → scope + section count + `_full.pdf` name; 15.54 a rendering failure → `failure` with the 500 and no delivered size; 15.55 a log the caller may not see → `failure` keeping the 404's traceback, nothing attached) |
| Fixtures | `_CalcLogTestCase` (already carries `CalculationLog` + `AuditLog` + `AuditLogStatus`); `render_pdf_bytes` stubbed to fail for 15.54; the superuser flag dropped for 15.55 |
| Tests landed | **4 pass / 0 fail** |
| Coverage gain | audit coverage for the PDF endpoint's two scopes and both refusal paths |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-22-export-download-audit.md) |

---

### Batch 15m — A generated row's log is filed under that row: synchronous routes ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.56 – 15.62 |
| Type | I |
| Files covered | `lex/core/mixins/CalculatedModelMixin.py` (`calc_and_save_streaming`, `calc_and_save_sync`), `lex/core/models/CalculationModel.py` (`child_calculation_context`), `lex/audit_logging/models/CalculationLog.py` (rows resolved from the frame stack, rows of unsaved models kept on the instance, a parent's first row created under its own parent), `lex/audit_logging/utils/ModelContext.py` (rows linked when a block ends) |
| Test file | `lex/test_project/tests/calculation_logging/test_15m_generated_row_log.py` |
| Test classes | `TestCluster15m_GeneratedRowLog` — 15.56 materialising route (`LEX_SYNC_STREAMING_EXPANSION=false`); 15.57 / 15.58 a non-atomic root on the streaming / materialising route, where lines are written before a new row has a pk; 15.59 a second run filed under the stored rows; 15.60 / 15.61 a middle that logs only after its grandchild, atomic / non-atomic root; 15.62 a failing row keeps its lines in its own node and leaves no frame on the stack |
| Fixtures | `_CalcLogTestCase`; new `LogFailingChild`, `LogQuietMiddle` (`GENERATED_ROW_MODELS`, kept out of `ALL_MODELS`); `LogRootCalc` modes `fail_child_then_log` and `quiet_three_level`; a non-atomic root through `patch.object(LogRootCalc, "is_atomic", False)`; each route confirmed by watching its consumer with `wraps=` |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete — fixes [BUG-052](../../known-bugs.md) and [BUG-053](../../known-bugs.md); see [session note](../../progress/sessions/2026-11-06-fix-calc-expansion-child-logs.md) |

**Why 15b–15e failed for six months without anyone acting on it.** `eec74b6d` (2026-05-14) replaced
`model_logging_context(model)` with `calculation_execution_context()` in `calc_and_save_sync` and in
Celery's `calc_and_save`, when the two were meant to be nested. `calc_and_save_streaming` (2026-06-10)
copied the result. The nine scenarios that pinned the nesting failed from that day: they pass on
`eec74b6d~1` (18 pass / 3 xfail) and fail on `eec74b6d`. No CI job ran the cluster, and one session
note filed the failures as cross-test interference. All three consumers now go through one helper,
`child_calculation_context`, so they cannot drift apart again, and the cluster is in the release gate.

**Each scenario was checked to fail with its part of the fix undone.** All nine of 15m and 15n fail on
v2.4.2's consumers. Pushing the row without keeping a node on an unsaved instance fails 15.57, 15.58,
15.61, 15.63 and 15.64. Pushing the generated copy instead of the stored row fails 15.59. Creating a
parent's first row detached, as before, fails 15.60.

---

### Batch 15n — A generated row's log is filed under that row: Celery route ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.63 – 15.64 |
| Type | I |
| Files covered | `lex/lex_app/celery_tasks.py` (`calc_and_save`), `lex/core/tasks/CeleryTaskDispatcher.py` (stack copy per group), `lex/core/models/CalculationModel.py` (`child_calculation_context`, `dispatch_calculation_task`), `lex/audit_logging/models/CalculationLog.py` (`prepare_for_dispatch`), `lex/audit_logging/utils/ModelContext.py` (`copy_for_dispatch`, `ModelContext.has_on_top`) |
| Test file | `lex/test_project/tests/calculation_logging/test_15n_celery_route_log.py` |
| Test classes | `TestCluster15n_CeleryRouteLog` — 15.63 a root running in the generic `calc_and_save` task is one node, not nested under the copy of itself it was shipped with, and each dispatched row gets its own node; 15.64 a middle row that dispatches its grandchildren before logging is one node for every worker |
| Fixtures | `_celery_eager()` from cluster 8h (real `.delay()`, wrapper and dispatcher; Celery's own eager mode instead of a broker); new `LogCeleryLoudChild`, `LogCeleryGrandchild`, `LogCeleryQuietMiddle` with `@lex_shared_task` `calculate()` under explicit task names (`CELERY_ROUTE_MODELS`); both dispatch points watched with `side_effect=` to confirm the Celery route |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-fix-calc-expansion-child-logs.md) |

**What a worker changes.** It writes every line immediately (no transaction defers it until the row is
saved) and gets a copy of the stack. The generic task's root finds a copy of itself on top, which is why
`child_calculation_context` skips an instance that already is the top frame. A middle row that
dispatches before logging is unsaved when the copy is made; `copy_for_dispatch` creates its node first,
so the id travels with the copy. Removing the guard fails 15.63 and 15.64; removing the node created
before the copy fails 15.64.

---

### Batch 15o — A nested CalculationModel's synchronous run is not taken for the root ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.65 – 15.71 (and 15.19 – 15.21 of 15f, whose `@expectedFailure` markers are dropped) |
| Type | I (15.70: E) |
| Files covered | `lex/core/models/CalculationModel.py` (`calculate_hook` opens `child_calculation_context` in its synchronous branch and closes it in its `finally`; `execute_calculation_sync`'s terminal bookkeeping and the hook's failure handling now run inside that frame), `lex/api/views/model_entries/One.py` (`_calculate_in_background`, as the route 15.70 drives), `lex/lex_app/celery_tasks.py` (`calc_and_save`, unchanged, as the route 15.71 drives) |
| Test file | `lex/test_project/tests/calculation_logging/test_15o_nested_calculation_run.py` |
| Test classes | `TestCluster15o_NestedCalculationRun`: 15.65 the run's live log, read after a nested model finished, still holds the root's line; 15.66 a run that fails in a nested model keeps its log as one row on the root; 15.67 a nested failure the root handles leaves one root node, with no copy of the root's lines elsewhere; 15.68 a root saved with nothing on the stack gets one row keyed to its record; 15.69 such a root keeps its log when it fails; 15.70 Calculate through the REST API (PATCH `calculate=true`, background thread) gives the root one row and the nested model its own; 15.71 the same tree on the Celery route (eager) |
| Fixtures | `_CalcLogTestCase` with the real live cache (`store_message`, `build_cache_key` unpatched, as in 15j); `_live_log_purged_like_redis()` answers `cleanup_calculation`'s key lookup with the run's keys, so a purge deletes them as on Redis (the test cache cannot match patterns); new `LogFailingInnerSavedCalc` (`NESTED_RUN_MODELS`, kept out of `ALL_MODELS`); `LogRootCalc` modes `calcmodel_then_read_live_log`, `failing_calcmodel`, `failing_calcmodel_caught`; `_celery_eager()` from cluster 8h with `LogInnerSavedCalc.dispatch_calculation_task` watched to confirm the Celery route |
| Tests landed | **7 pass / 0 fail** (15f: 3 pass, was 3 xfail) |
| Status | ✅ Complete — fixes [BUG-055](../../known-bugs.md); see [session note](../../progress/sessions/2026-11-09-sync-route-own-frame.md) |

**Why the frame has to outlast the run.** The terminal bookkeeping in `execute_calculation_sync`
(`finally`) and the failure handling in `calculate_hook` (`except`) both ask the stack whether this
run is the root: `ContextResolver`'s `root_record == current_record`. Only the root may purge the
run's live log (cluster 9c) or write it back when the run fails (15j). A model the root saved, with no
frame of its own, found the root on top of the stack as well as at its bottom, and answered yes. The
frame is therefore opened in the hook and closed in its `finally`, not around `calculate()` alone.
The developer chose this scope over the narrower fix that 15f's docstring suggested.

**Each scenario was checked to fail with its part of the fix undone.** Without any frame, 15.65 – 15.70
and 15.19 – 15.21 fail. With a frame around `calculate()` only, 15.65, 15.66, 15.67 and 15.69 fail. With a
frame that ends before the failure handling, 15.66 and 15.67 fail. With a frame pushed even when the
model is already on top, 15.19 – 15.21, 15.67 and 15.70 fail. 15.71 passes without the fix as well:
since the fix for BUG-052, the generic Celery task gives a nested model its own frame. It guards that
the routes agree.

---

### Batch 15p — A CalculationModel dispatched to Celery runs in a frame of its own ✅

| Property | Value |
| --- | --- |
| Scenario range | 15.72 – 15.77 |
| Type | I |
| Files covered | `lex/core/models/CalculationModel.py` (`calculate_hook` opens `child_calculation_context` before it either dispatches the run or runs it, and closes it in its `finally`; `dispatch_calculation_task` ships the stack with the model on top), `lex/lex_app/celery_tasks.py` (the `lex_shared_task` wrapper runs a decorated `calculate()` on that stack, `calc_and_save` skips the model already on top, `CallbackTask` reads its root check from it) |
| Test file | `lex/test_project/tests/calculation_logging/test_15p_decorated_celery_run.py` |
| Test classes | `TestCluster15p_DecoratedCeleryRun`: 15.72 a nested model whose `calculate()` is a `@lex_shared_task` gets its own node under its caller's; 15.73 a `CalculationModel` that such a model saves on the worker nests under it; 15.74 a decorated root saved with nothing on the stack gets one row keyed to its record, and its audit entry names it and closes as a success; 15.75 the generic `calc_and_save` task gives such a root the same row; 15.76 / 15.77 a nested failure the root handles leaves the run's live log alone, on the generic task / on the decorated route, where the nested model's line also stays out of the root's node |
| Fixtures | `_CalcLogTestCase` with the real live cache, as in 15o; `_celery_eager()` from cluster 8h; `_live_log_purged_like_redis()` from 15o; a `task_prerun` recorder that names the tasks Celery ran, to confirm each route; new `LogCeleryInnerSavedCalc`, `LogCeleryFailingInnerSavedCalc`, `LogCeleryMiddleSavedCalc`, `LogCeleryRootCalc` with `@lex_shared_task` `calculate()` under explicit task names (`DECORATED_RUN_MODELS`, kept out of `ALL_MODELS`); `LogRootCalc` modes `celery_calcmodel_chain`, `celery_calcmodel_chain_3`, `failing_caught_read_live_log`, `celery_failing_caught_read_live_log`; `LogRootCalc.live_log_after_inner` is now a field, because on Celery `calculate()` runs on the task's copy of the root and only what is saved comes back |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete — fixes [BUG-056](../../known-bugs.md); see [session note](../../progress/sessions/2026-11-09-decorated-celery-route-frame.md) |

**Why the frame has to be opened before the dispatch.** BUG-055's frame was opened only in the
synchronous branch, so on Celery the task was shipped a copy of the caller's stack, with the caller on
top. The generic `calc_and_save` task pushes the model it runs, which is why 15.71 passed, but the
`lex_shared_task` wrapper runs a decorated `calculate()` on the stack it is shipped, and pushes nothing.
The hook's failure handling runs in the caller's thread when the task failed, and asked the caller's
stack whether the run was the root: on both routes it found the caller on top and at the bottom, and
purged the run's live log while the root was still running. Opening the frame before the dispatch puts
the model on top of the shipped stack, the stack the docs' manual `model_logging_context` wrap already
produced, and keeps it there until the failure handling is done. `CallbackTask`'s root check reads the
shipped stack too, and gives the same answers as before: the root's own frame is at the bottom.

**The developer chose this over a push in the `lex_shared_task` wrapper.** That alternative fixes
15.72 – 15.74 but not 15.76 and 15.77: the failure handling never sees a frame pushed on the worker.

**Each scenario was checked to fail with its part of the fix undone.** With no frame on the Celery
route (the 15o head), 15.72 – 15.74, 15.76 and 15.77 fail. With a frame that ends before the failure
handling, 15.76 and 15.77 fail. With the wrapper alternative instead of the fix, 15.76 and 15.77 fail.
With `calc_and_save` pushing a model already on top, 15.72, 15.73 and 15.75 – 15.77 fail, and so do
15.63, 15.64 and 15.71. 15.75 passes without the fix: it guards that the routes agree.

---
