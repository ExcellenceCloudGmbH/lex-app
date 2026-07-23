---
date: 2026-07-07
clusters: [15g, 6q]
tests_added: "12 (15.22–15.31, 6.117–6.118) + source in 6 files"
suite_tally: "15g 10 pass / 0 fail; 6q 2 pass / 0 fail; regression: full `audit_logging` cluster = 141 pass / 1 xfail / 0 fail"
---

**Batches 15g and 6q landed — paired tests for the heading-context feature
(`feat/calclog-heading-context`).** `model_logging_context` now accepts a plain
string, pushing a `LogHeading` frame that produces a table-of-contents style
node in the calculation log tree: a `CalculationLog` row with the new nullable
`heading` field set and `content_type`/`object_id` NULL, keyed by
`(calculationId, audit_log, heading, parent_log)` and created lazily on the
first LexLogger flush inside the block (silent headings never persist). Routing
records (Redis cache keys, WebSocket groups) and root-calculation detection
resolve via the new `get_root_model()`/`get_current_model()` helpers so heading
frames stay presentation-only. Design spec:
`docs/superpowers/specs/2026-07-07-calclog-heading-context-design.md`.

[Batch 15g](../../clusters/15-calculation_logging/batches.md) covers the tree
surfaces (stack frames, lazy persistence, chains, node reuse, routing,
`__str__` titles); companion [batch 6q](../../clusters/06-audit_logging/batches.md)
pins that `_is_root_calculation` ignores heading frames. Source:
`lex/audit_logging/utils/ModelContext.py`, `utils/ContextResolver.py`,
`utils/DataModels.py`, `utils/calculation_audit.py`,
`models/CalculationLog.py`, migration `0007_calculationlog_heading`.
