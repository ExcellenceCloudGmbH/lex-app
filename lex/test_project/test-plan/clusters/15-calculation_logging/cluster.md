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
| 15f | `test_15f_save_boundary.py` | 15.19 – 15.21 | Hierarchy preservation across the `.save()` boundary — pins the EXPECTED behaviour that every `CalculationModel`'s `calculate()` runs with itself on top of the context stack. All three are `@unittest.expectedFailure`: the framework does not yet push `self` in `CalculationModel.execute_calculation_sync`, so inner `CalcModel.save()` collapses its log text onto the outer model's row. Fix: wrap `func()` in `model_logging_context(self)`; the markers drop when that lands. |
