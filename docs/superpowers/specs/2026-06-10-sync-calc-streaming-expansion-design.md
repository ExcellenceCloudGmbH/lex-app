# Sync-Mode Calculation Streaming Expansion — Design

> **Date:** 2026-06-10
> **Status:** Approved (pending spec review)
> **Author:** Claude (Opus 4.6) with Hazem Sahbani
> **Topic:** Reduce peak memory of `CalculatedModelMixin.create()` in synchronous
> mode (`CELERY_ACTIVE=False`) to stop backend OOM crashes.

---

## 1. Problem

When `CELERY_ACTIVE=False` there are no Celery worker pods — a calculation runs
**inside the web/ASGI process**, so the calc and the web server share one memory
budget. If the calc's peak allocation exceeds the pod's limit, Linux OOM-kills
the whole process and the website dies with it. Users sometimes run lex without
Celery, so this path must be safe on its own.

The dominant sync-mode memory vector is the **combinatorial expansion pipeline**
in `lex/core/mixins/CalculatedModelMixin.py`. `create()` runs four stages, and
**each stage materializes a full N-wide structure of all combinations** before
handing off to the next — nothing streams. For N defining-field combinations the
peak is *all N model instances alive at once, for the entire calc run*:

| Stage | Method | Holds | Evidence (file:line) |
|---|---|---|---|
| 1. Generate | `_expand_models_for_field` | builds N via `deepcopy`; transiently ~2N (list-of-lists `expanded_models` + flattened `result`) | `CalculatedModelMixin.py:374, 399, 413` |
| 2. Prepare | `_prepare_models_for_processing` | a second N-list `prepared_models`; one DB query + `.count()` per model | `:1386-1409, 1686-1687` |
| 3. Cluster | `_create_processing_clusters` | nested dict referencing all N again | `:1458` |
| 4. Dispatch (sync) | `_dispatch_model_processing` else-branch | flattens clusters back into one flat `all_models` list, then `calc_and_save_sync` loops it | `:1601-1609, 869` |

The model **objects** are the real cost; the extra lists/dicts mostly hold 8-byte
references to the same objects. The killer is **lifetime**: `calc_and_save_sync`
iterates a fully-materialized list, so even though it processes one model at a
time, none can be garbage-collected until the whole run finishes, and whatever
each `calculate()` pins onto `self` stays alive too.

Two structural observations:

- **Clustering (stage 3) is pure waste in sync mode.** Stage 4's sync branch
  immediately flattens the cluster dict back into one flat list. Clusters exist
  only to group models for Celery dispatch.
- **The expansion is dependency-aware, not a plain Cartesian product.**
  `_get_field_values` calls `get_selected_key_list` on the *partially-built*
  model (`:472`), so a later field's allowed values can depend on an
  earlier field's chosen value. Any streaming rewrite **must preserve this DFS
  semantics** — a naive `itertools.product` over independently-computed value
  lists would silently produce a different set of combinations. This is the
  primary correctness trap.

### Out of scope (this design)

- The `.iterator()` / server-side-cursor memory vector (the cost of the
  `DISABLE_SERVER_SIDE_CURSORS` fix). That is "Step 1", a separate design.
- Per-`calculate()` user data loading (pandas frames, large reads). User code.
- The calc-log LocMemCache buffer (already bounded to 256 KB per key; sub-cluster
  6p).

---

## 2. Goals & Non-Goals

**Goals**
- Cut sync-mode peak memory of the expansion pipeline from **O(N models)** to
  **O(depth = number of defining fields)** — only the current root-to-leaf path
  alive at once.
- Prove the new path produces a **byte-identical set of models, in the same
  order**, as the legacy path (equivalence gate).
- Ship a **memory benchmark** (old vs new) as the first deliverable, sized to
  **N ≈ 10,000** combinations.

**Non-Goals**
- Do **not** change Celery-mode behavior. The list/cluster pipeline (stages 1–3)
  and the Celery dispatch branch stay exactly as they are.
- Do not optimize user `calculate()` weight.
- Do not change the public `create()` / `get_selected_key_list()` / `calculate()`
  contract.

---

## 3. Part 1 — Benchmark Harness (first deliverable)

A standalone, repeatable harness that measures peak memory of the **current**
sync `create()` at N ≈ 10k **and** captures the exact produced-combination set so
the new path can be proven identical.

### Synthetic model
A `CalculatedModelMixin` subclass:
- **3 defining fields**, ~**22 values each** → 22³ = 10,648 ≈ 10k combinations.
- `parallelizable_fields = []` (forces the sync path).
- **Dependency-aware `get_selected_key_list`**: at least one field's value list
  depends on an already-set earlier field, so the harness exercises the DFS trap
  rather than a trivial Cartesian product.
- `calculate()` is a **cheap no-op** — we are measuring *framework* expansion
  overhead (lists/deepcopies/lifetime), not user-calc weight. (We considered
  having `calculate()` allocate a representative payload; decided against it for
  the baseline so the framework signal isn't masked. A payload knob can be added
  later if needed.)

### What it records per run
- **Peak memory** via `tracemalloc` (peak bytes + a sampled growth curve across
  the four pipeline stages).
- **Produced fingerprint**: an ordered list of each model's defining-field tuple
  `[(f0, f1, f2), ...]` — the equivalence fingerprint — plus the saved-row count.
- Wall time (secondary).

### Environment
- Runs under the lex test env, sync mode (`CELERY_ACTIVE` unset).
- **In-memory SQLite** so DB I/O doesn't dominate, while still executing
  `delete_models_with_same_defining_fields`'s per-model query so that cost stays
  visible.
- Invokable as a pytest function / management-style script.

### Output
A small table — `baseline` vs `streaming` × {peak MB, wall time, model count} —
printed and written under `docs/runs/` so old-vs-new is diffable.

---

## 4. Part 2 — Streaming Sync Path (the fix)

Keep the existing list/cluster pipeline for **Celery mode untouched**; add a
**DFS generator** that the **sync path** consumes one model at a time.

### 4.1 Streaming DFS generator (new)
A generator that mirrors `_expand_models_for_field` value-for-value and
copy-for-copy, but depth-first with `yield` instead of breadth-first into a list:

```
def _generate_combinations_streaming(base_model, defining_fields, field_overrides):
    # ordered_defining_fields = same ordering as today (override fields first)
    # recursive DFS:
    #   at each level, compute field_values from the PARTIALLY-built model via the
    #   SAME _get_field_values / _normalize_field_values helpers (so dependency-aware
    #   get_selected_key_list still sees earlier fields);
    #   deepcopy-per-value at the same granularity as today;
    #   recurse to the next field;
    #   yield a model only once ALL defining fields are set (a leaf).
```

Invariant: **never more than one root-to-leaf path (≤ depth models) alive.**
Reuses the existing `_get_field_values` and `_normalize_field_values` so value
computation, normalization, pruning (empty value list → branch dropped), and
error wrapping match the legacy generator exactly.

### 4.2 Streaming sync consumer (new)
A streaming sibling of `calc_and_save_sync`. Per yielded model it runs, inline:
1. **Stage-2 prepare** — `delete_models_with_same_defining_fields()` (dedup +
   pk reset), exactly as the legacy stage 2 does per model.
2. `calculate()` + `save()` inside the existing `calculation_execution_context()`
   (identical to `calc_and_save_sync`'s per-model body, `:882-897`).
3. Drop the reference and pull the next model.

Stages 2's separate `prepared_models` list and stage 3's cluster dict are
**skipped entirely in sync mode** — dead weight there.

### 4.3 Wiring
The memory win requires bypassing stages 1–3 in sync mode (if they still run,
the full N-list is built before streaming ever starts). So `create()` branches
on mode **once, at the top**, using the same `CELERY_ACTIVE`/`delay` detection
`_dispatch_model_processing` already uses (`:1529`):

- **Celery mode** → unchanged: run stages 1–3 (`_generate_model_combinations` →
  `_prepare_models_for_processing` → `_create_processing_clusters`) then the
  Celery branch of `_dispatch_model_processing`. **Zero diff to this path.**
- **Sync mode** → route directly to the streaming generator + streaming consumer
  from the `base_model` and `defining_fields`/overrides, **skipping**
  `_generate_model_combinations` / `_prepare_models_for_processing` /
  `_create_processing_clusters` and the `else` branch of
  `_dispatch_model_processing` (which built `all_models` →
  `calc_and_save_sync`).

So two things change in `create()`: a top-level mode branch, and the sync side
calls the new streaming code instead of the four-stage pipeline. The Celery side
is byte-identical to today. The legacy `calc_and_save_sync` and the sync `else`
branch remain in the file (reachable via the `=false` valve, see §4.4) but are no
longer the default sync route.

### 4.4 Rollout / safety valve
Streaming becomes the **default sync behavior** (the equivalence gate proves
byte-identical output, and OOM relief is the whole point — a flag defaulting off
would not relieve anything out of the box). An **escape-hatch env var**
`LEX_SYNC_STREAMING_EXPANSION` (default `true`) lets an operator force the legacy
materialized path (`=false`) if a regression surfaces in the field. This keeps a
one-line rollback without a redeploy.

---

## 5. Equivalence Guarantee (how we "don't break logic")

The Part-1 fingerprint is the gate. For the synthetic model **and** for the
existing E2E calc fixtures, the **ordered list of produced defining-field tuples
and the saved-row set/count must be identical** between legacy and streaming
paths. If they differ, the streaming DFS is wrong and must be fixed before merge.

### The one behavioral nuance (explicitly accepted)
Legacy generates **all** combinations and prepares them **before any**
`calculate()` runs. Streaming **interleaves**: model #1 is calculated and
**saved** before model #2 is generated. For independent calcs — the documented
contract ("calculations should be deterministic for the same field values",
`:1160`) — this is invisible, and Celery mode already interleaves. The single
observable difference would be a user whose `get_selected_key_list` or
`calculate()` secretly depended on seeing the DB in the fully-pre-expanded-but-
not-yet-saved state. That is considered already-unsupported. The
`LEX_SYNC_STREAMING_EXPANSION=false` valve covers any such case.

---

## 6. Testing

Per the lex-testing skill, framework changes under `lex/` need paired cluster
tests in the same change.

- **Equivalence tests**: drive both paths (legacy via valve `=false`, streaming
  via default) over the synthetic dependency-aware model; assert identical
  ordered fingerprint + saved-row count. Include dependency-aware fields, field
  overrides, empty-value pruning, and the no-defining-fields single-model case.
- **DFS-semantics tests**: a field whose values depend on an earlier field —
  assert streaming sees the partially-built model (the trap).
- **Memory assertion (light)**: a scaled-down N where we can assert peak stays
  bounded (≈ O(depth)) rather than scaling with N — guards the regression.
- **Benchmark harness** itself runs at N≈10k for the manual old-vs-new report
  (not a CI gate; the light memory assertion is the gate).
- Cluster/scenario numbers allocated by the lex-testing skill at implementation
  time.

---

## 7. Affected Files

- `lex/core/mixins/CalculatedModelMixin.py` — add `_generate_combinations_streaming`
  + streaming sync consumer; route sync mode in `create()` /
  `_dispatch_model_processing`; read `LEX_SYNC_STREAMING_EXPANSION`.
- `lex/lex_app/settings.py` — (only if the flag is surfaced as a setting; likely
  read directly from env to match existing `CELERY_ACTIVE` style).
- Benchmark harness — new file under `scripts/` (e.g.
  `scripts/bench_sync_expansion.py`), pytest-invokable, writing its report to
  `docs/runs/`.
- Paired cluster test file(s) under `lex/test_project/tests/…`.
- Plan-file sync (dashboard, session-log, test-clusters, test-writing-plan).

---

## 8. Success Criteria

1. Benchmark shows streaming peak memory is materially lower than baseline at
   N≈10k and grows ~flat with N (vs baseline's linear growth).
2. Fingerprint identical between legacy and streaming for synthetic + E2E models.
3. Celery-mode code path unchanged (no diff to the Celery branch / stages 1–3 as
   used by Celery).
4. `LEX_SYNC_STREAMING_EXPANSION=false` reproduces legacy behavior exactly.
