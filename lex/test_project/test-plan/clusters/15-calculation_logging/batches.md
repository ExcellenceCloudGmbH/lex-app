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
