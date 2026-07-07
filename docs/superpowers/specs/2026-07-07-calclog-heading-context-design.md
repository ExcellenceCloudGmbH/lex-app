# Calculation Log Heading Context — Design

**Date:** 2026-07-07
**Status:** Approved (user), implementation on `feat/calclog-heading-context` off `lex-app-v2`
**Constraint:** No tests in this round per user instruction (tests follow after the pending PR merges).

## Problem

`model_logging_context(instance)` connects `CalculationLog` rows parent-to-child, producing the
execution tree shown in the frontend's Calculation Log page. Every tree node must currently be a
Django model instance. Projects want table-of-contents style grouping inside a calculation —
titled sections that nest log output without needing a backing object:

```python
with model_logging_context("Data preparation"):
    CalculationLog.log("Loading input files...")
    with model_logging_context("Validation"):
        CalculationLog.log("Checking schemas...")
```

## Decisions (user-confirmed)

1. **API shape:** reuse `model_logging_context` — a plain `str` argument means "heading frame".
   No new public API.
2. **Node creation:** lazy — the heading's `CalculationLog` row is created on the first
   `CalculationLog.log()` inside the block. Silent heading blocks never appear in the tree.

## Design

### 1. Stack marker — `LogHeading`

New class in `lex/audit_logging/utils/ModelContext.py`:

- Holds `title` (the heading text) and `node_id` (cached pk of its persisted `CalculationLog`
  row, set on first persistence).
- `model_logging_context` wraps `str` arguments in `LogHeading`; model instances behave exactly
  as today; other types still raise `TypeError`.
- Plain Python object → survives the `deepcopy` used when shipping the model context to Celery
  workers (`CalculationModel._dispatch_async`, `CeleryTaskDispatcher`).

`ModelContext` gains two helpers used everywhere frames must be real models:

- `get_root_model()` — first non-heading frame from the bottom of the stack.
- `get_current_model()` — first non-heading frame from the top of the stack.

### 2. Persistence — `CalculationLog`

- New nullable field: `heading = models.TextField(null=True, blank=True)` + migration
  `audit_logging/0007_calculationlog_heading.py`.
- A heading node is a row with `content_type`/`object_id` NULL and `heading` set, keyed by
  `(calculationId, audit_log, heading, parent_log)`. Same title under different parents = two
  nodes; re-entering the same heading under the same parent reuses the node (idempotent, matching
  model-node behavior).
- `__str__` returns `heading` when set, else the Django default `CalculationLog object (pk)`
  string — the tree serializer titles nodes with `str(obj)`, and the frontend passes non-generic
  titles through untouched, so **no frontend change is required**.

### 3. Context resolution — `ContextResolver` / `ContextInfo`

- `ContextInfo` gains `frames: Optional[List[Any]]` — a snapshot (`list(stack)`) of the model
  context stack taken at log time. The snapshot matters because persistence may be deferred via
  `transaction.on_commit`, after the with-blocks have already exited.
- `current_model` / `content_type` describe the **actual top frame**: `None`/`None` when the top
  frame is a heading (the current node is then the heading node).
- `parent_model` / `parent_content_type` become "nearest model frame below the top" (this is the
  base node the heading chain hangs off; identical to today when no headings are present).
- `current_record` / `root_record` (Redis cache keys + WebSocket group routing) come from the
  **nearest real model frames** (`get_current_model()` / `get_root_model()`), so live streaming
  and caching behave exactly as today — headings affect only tree structure.

### 4. Persistence walk — `CalculationLog._persist_message`

When `frames` is present:

1. Ensure the nearest model parent node exactly as today (lookup without `parent_log`).
2. Ensure each heading frame between that model and the top, in order, each with
   `parent_log` = previously ensured node; cache each pk in `LogHeading.node_id` so subsequent
   logs cost no extra queries.
3. Ensure the current node: heading frame → heading lookup; model frame → today's lookup
   (`calculationId, audit_log, content_type, object_id, parent_log`), unchanged.

Legacy behavior (no `frames`) is kept as a fallback so older callers of `ContextInfo` keep
working.

### 5. Consumers hardened against heading frames

- `CalculationLog.log()` `ContextResolutionError` fallback: route WebSocket messages via
  `get_current_model()` / `get_root_model()` instead of raw `current` / `get_root`.
- `calculation_audit._is_root_calculation`: compare against `get_root_model()` /
  `get_current_model()` (with `getattr` fallbacks preserved) so a heading on the stack cannot
  break root-calculation detection.

## Rejected alternatives

- **Separate `logging_section()` API** — second API to document; user chose reuse.
- **No schema change** (encode title in `calculation_log` text or synthetic content_type) —
  breaks title rendering, Details view, and node identity; fragile.
- **Eager creation on context enter** — produces empty TOC entries and requires an active
  calculation context at enter time.

## Testing (deferred)

After the pending PR merges: paired cluster tests via the `lex-testing` skill — stack behavior
for `LogHeading` and `model_logging_context(str)`, resolver skipping, persistence of nested
model→heading→heading chains, lazy creation, idempotent re-entry, and routing records ignoring
headings.
