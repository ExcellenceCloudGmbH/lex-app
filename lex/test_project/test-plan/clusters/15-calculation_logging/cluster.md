## 15. Calculation Logging Surface

**Promoted out of cluster 7 during the 2026-07-07 restructure.** The
`calculation_logging/` test files self-number as `test_15<letter>_*.py` with
`Scenario 15.M` docstrings, so the de-facto truth on disk is a cluster-15
identity. Before the restructure the folder was informally attached to cluster 7
as a "logging surface", which made the per-cluster `validate` gate report every
file as "filename cluster 15 but folder belongs to cluster 7". Rather than
renumber six test files into cluster 7's scenario space, the plan follows reality:
`calculation_logging` is now its own cluster 15, dropped from cluster 7's
`test_dirs`.

**What it tests:** How the `LexLogger` builder and `model_logging_context` stack
produce `CalculationLog` rows through the real calculation pipeline — the content
shape of a single log, parent/child hierarchy nesting, silent (non-logging)
children, three-level chains, the combinatorial fan-out pipeline, and hierarchy
preservation across the `CalculationModel.save()` boundary.

**Scenario table:**

| # | File | Scenarios | Purpose |
|---|------|-----------|---------|
| 15a | `test_15a_builder_api.py` | 15.1 – 15.6 | LexLogger builder API and content shape — exercises LexLogger directly under a manual `model_logging_context` wrapper (does NOT go through `CalculationModel.save()`). |
| 15b | `test_15b_child_node_creation.py` | 15.7 – 15.10 | Single-parent hierarchy, loud children — full pipeline: `LogRootCalc.save()` → `calculate_hook` → `calculate()`; `LogLoudChild.create()` runs combinatorial expansion through `calc_and_save_sync`. |
| 15c | `test_15c_silent_and_mixed.py` | 15.11 – 15.13 | Silent children + mixed-siblings regression gate — 15.12 pins the production "divergent child calculation" investigation: a child that never calls LexLogger produces ZERO `CalculationLog` rows even though `calc_and_save_sync` wraps it in `model_logging_context`. |
| 15d | `test_15d_three_level_chain.py` | 15.14 – 15.15 | Three-level chain (root → middle → grandchild) — pins that `parent_log` is the IMMEDIATE stack parent at each level, not the root. |
| 15e | `test_15e_combinatorial_pipeline.py` | 15.16 – 15.18 | CalculatedModelMixin combinatorial pipeline — verifies `calc_and_save_sync` wraps every combinatorial instance, per-instance row keying is correct, and conditional logging within one fan-out produces exactly the expected subset of rows. |
| 15f | `test_15f_save_boundary.py` | 15.19 – 15.21 | Hierarchy preservation across the `.save()` boundary — every `CalculationModel`'s `calculate()` runs with itself on top of the context stack, so an inner `CalcModel.save()` gets its own node under the caller, two and three levels deep and below a `create()` fan-out. `@unittest.expectedFailure` from 2026-05-12 until the fix for BUG-055, which runs a synchronous calculation inside `child_calculation_context` from `calculate_hook`. |
| 15g | `test_15g_heading_context.py` | 15.22 – 15.31 | Object-less heading frames (TOC nodes) — `model_logging_context("...")` pushes a `LogHeading` frame; heading rows persist lazily on first LexLogger flush (`heading` set, `content_type`/`object_id` NULL), chain correctly under models/headings, reuse nodes on re-entry, stay distinct across parents; routing records and `__str__` tree titles skip/surface headings correctly. |
| 15k | `test_15k_create_triggered_run_log.py` | 15.51 | A run started by creating a record (`calculate_on_create`) is filed under `<model>_<pk>_update_<uuid>`, so the list view's latest-run lookup finds it and the new row's log button opens it — driven end to end through the REST create and list endpoints. |
| 15l | `test_15l_pdf_download_audit.py` | 15.52 – 15.55 | Downloading a calculation log as PDF is recorded in the audit trail — an entry against the log itself with the PDF's name, size and whether the subtree was included, and a `failure` entry (keeping the traceback) when the render fails or the log is not the caller's to see. |
| 15r | `test_15r_pdf_download_scopes.py` | 15.83 – 15.89 | The calculation-log PDF answers `?scope=node\|subtree\|calculation` — one step, that step and everything under it, or every log of the calculation (the whole Execution tree, all roots titled) — with `include_descendants=true` kept as the older spelling, an unknown scope refused with an audited 400, and an oversized run cut visibly. |
| 15q | `test_15q_calculation_log_read_surfaces.py` | 15.78 – 15.82 | Every way the frontend reads a `CalculationLog` row — by id, through the AG Grid endpoint and through `many` — serves a plain (non-superuser) caller. The serializer's remembered-decision branch asked `type(instance).can_read` before asking whether the model has one, and `CalculationLog` is a plain `django.db.models.Model`: the table answered 500 on every read, which the log drawer's Details tab showed as a redirect to the table and the table showed as `ERR` in every cell. 15.81 guards that a `LexModel` still reaches that branch; 15.82 pins the drawer's own audit query and the traceback it reads off it. |
| 15m | `test_15m_generated_row_log.py` | 15.56 – 15.62 | A row generated by `create()` has its log filed under that row on every synchronous route: the materialising route, a non-atomic root whose lines are written before a new row has a pk (one node per row, linked to its record once saved, siblings never merged), a re-run keyed by the stored rows, a middle that logs only after its grandchild (one node, under the root), and a failing row that keeps its lines in its own node and leaves no frame behind. |
| 15n | `test_15n_celery_route_log.py` | 15.63 – 15.64 | The same tree when the work runs in Celery tasks (eager): a root dispatched through the generic `calc_and_save` task is not nested under a copy of itself, each dispatched row gets its own node, and a middle row that dispatches before logging is one node for every worker. |
| 15o | `test_15o_nested_calculation_run.py` | 15.65 – 15.71 | A nested `CalculationModel`'s synchronous run keeps its own frame through its terminal bookkeeping and failure handling, so it is not taken for the root. It leaves the run's live log alone when it finishes, a run that fails in it keeps its log, and a failure the caller handles leaves one root node. A root saved with nothing on the stack gets a node of its own and keeps its log when it fails. The API route (background thread, copy of the request context) does not push the root twice, and the Celery route gives the same tree. |
| 15p | `test_15p_decorated_celery_run.py` | 15.72 – 15.77 | A `CalculationModel` dispatched to Celery runs in a frame of its own, whether its `calculate()` is a `@lex_shared_task` or runs in the generic `calc_and_save` task (eager Celery). A nested decorated model gets its own node, and what it saves on the worker nests under it. A decorated root saved with nothing on the stack gets a node keyed to its record and closes its audit entry as a success, and the generic task gives such a root the same node. On both routes, a nested failure the root handles leaves the run's live log alone. |
