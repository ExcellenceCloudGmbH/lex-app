# Liveness-aware startup calculation reset

> **Date:** 2026-06-08
> **Status:** Design — pending implementation
> **Author:** Hazem Sahbani (with Claude)

## Problem

When the backend pod restarts while calculations are running, every row left in
`IN_PROGRESS` is flipped to `ABORTED` on boot — even though the Celery worker
pods are **separate** and keep running the work to completion. The operator sees
calculations that are still computing reported as `ABORTED`, and when a worker
finishes it can flip the row back to `SUCCESS`/`ERROR` (a resurrection).

The requirement: **any pod restart must leave a path to recover — no calculation
state is silently lost.**

## Root cause

`ProcessAdminModelRegistration._handle_calculation_model_reset`
(`lex/process_admin/utils/model_registration.py`) runs on boot
(`CALLED_FROM_START_COMMAND`) and does a **blind, unconditional** sweep:

```python
stuck = model.objects.filter(is_calculated=IN_PROGRESS)
for instance in stuck:
    instance.is_calculated = ABORTED
    instance.save(skip_hooks=True)
    ensure_terminal_calculation_audit(instance, audit_status="aborted", ...)
```

It assumes "backend restart ⇒ every in-progress calc is dead." In a split
web/worker deployment that is false: the worker pods survive a backend restart
and the broker redelivers messages to workers that died (`task_acks_late`,
`task_reject_on_worker_lost`). The sweep never consults the heartbeat registry
that already knows which tasks are in flight.

## Why "skip only live rows" is wrong (the #603 interaction)

A first instinct is: skip rows whose worker heartbeat is still warm, abort the
rest. That **violates the no-loss requirement**. A genuinely dead worker still
leaves its task **tracked** in the registry (the payload key lives 24h) with an
*expired* heartbeat — and that is exactly the state the recovery supervisor
(`scan_and_recover`) is built to requeue and resume. But PR #603 added a
terminal-outcome guard: if the row is already terminal, the supervisor skips the
requeue. So if the startup sweep aborts a dead-but-tracked row first, #603 then
blocks its resume — the calc is permanently lost.

Therefore the decision is not "alive vs dead." It is **"is this row owned by the
recovery machinery at all?"**

## Design

### Rule

For each stuck `IN_PROGRESS` row at startup:

| Row is… | Action | Who finishes it |
|---|---|---|
| owned by **any tracked task** (alive heartbeat *or* expired-but-tracked) | **leave `IN_PROGRESS`** | the live worker completes it, or the recovery supervisor requeues/resumes it |
| **not tracked** by any recovery task | **`ABORTED` + audit** (today's behavior) | nothing can resume it — abort is the honest terminal |

This hands every recoverable row to the existing, already-periodic recovery
supervisor (`sweep_dead_workers`, beat-scheduled in `settings.py`) and only
aborts rows that no mechanism could ever resume (e.g. an in-process synchronous
calc whose web process crashed, or any calc when recovery is disabled).

### Component 1 — `tracked_calculation_record_ids()` (new)

A pure helper in the recovery package (`celery_recovery/supervisor.py`, beside
the `_extract_calculation_models` it reuses):

```python
def tracked_calculation_record_ids() -> set[tuple[str, Any]]:
    """(_meta.label_lower, pk) for every CalculationModel row a tracked
    recovery task currently owns — alive or merely tracked."""
    out = set()
    for task_id in registry.list_tracked():
        payload = registry.get_payload(task_id) or {}
        for inst in _extract_calculation_models(payload.get("args")):
            if inst.pk is not None:
                out.add((type(inst)._meta.label_lower, inst.pk))
    return out
```

- No `is_alive` filter: both alive and expired-but-tracked tasks are owned by
  recovery, so both protect their rows from the startup abort.
- **Degrades safely:** when recovery is disabled or Redis is unreadable,
  `registry.list_tracked()` returns `[]` → empty set → every row is treated as
  orphaned → **today's blind-abort behavior, unchanged.** No regression when the
  machinery is off.

### Component 2 — the sweep consults it

`_handle_calculation_model_reset` gains an optional precomputed set so the
per-model loop in `register_models` reads the registry once, not once per model:

```python
def _handle_calculation_model_reset(cls, model, tracked_record_ids=None):
    ...
    if tracked_record_ids is None:
        from lex.lex_app.celery_recovery.supervisor import tracked_calculation_record_ids
        tracked_record_ids = tracked_calculation_record_ids()
    ...
    stuck = model.objects.filter(is_calculated=IN_PROGRESS)
    for instance in stuck:
        if (model._meta.label_lower, instance.pk) in tracked_record_ids:
            continue  # owned by recovery — never abort here
        instance.is_calculated = ABORTED
        instance.save(skip_hooks=True)
        ensure_terminal_calculation_audit(instance, audit_status="aborted", ...)
```

The `CALLED_FROM_START_COMMAND` gate, the audit-writing path, and the
`save(skip_hooks=True)` history mechanics are all untouched. The optional
parameter keeps the existing direct callers (and tests) working with no
signature break.

The caller computes the set once:

```python
tracked_ids = tracked_calculation_record_ids()   # once, before the model loop
...
if issubclass(model, CalculationModel):
    cls._handle_calculation_model_reset(model, tracked_record_ids=tracked_ids)
```

## Old-logic observations

- **`asyncio` / `nest_asyncio` / `run_until_complete` wrapper** around a
  `sync_to_async`-wrapped *synchronous* ORM function is convoluted, but
  `nest_asyncio.apply()` implies it is sometimes invoked inside a running event
  loop at startup. Changing it is out of scope and risky; **leave it as-is.**
- No other behavior in the sweep is altered. We add a filter, nothing more.

## Edge cases (all covered by tests)

1. Backend restart, worker alive & beating → row tracked → **skipped**, worker finishes.
2. Worker pod died, task still tracked (heartbeat expired) → row tracked → **skipped**, supervisor resumes.
3. Task never tracked (recovery off / sync in-process calc crashed) → **ABORTED + audit** (back-compat).
4. Recovery disabled / Redis down → `list_tracked()==[]` → all orphaned → **all ABORTED** (back-compat).
5. Tracked task whose payload has no calc instances → contributes nothing → unrelated rows still abort.
6. Mixed rows for one model: some tracked, some not → only the untracked ones abort.
7. Tracked task references a *different* row than the stuck one → stuck row not protected → aborts.
8. `CALLED_FROM_START_COMMAND` unset → whole sweep is a no-op (unchanged).
9. Row with `pk is None` never enters the tracked set (defensive).
10. Many CalculationModel subclasses → registry read once (precomputed set), correctness preserved.

## Test plan

Paired tests live under `lex/test_project/tests/` per the lex-testing skill
(cluster 8 — Celery & Async; next free letter). Two layers:

**A. `tracked_calculation_record_ids()` — SimpleTestCase, mocked registry**
- alive task with calc rows → ids returned
- expired-but-tracked task → ids still returned (no `is_alive` gate)
- empty registry / Redis down (`list_tracked()==[]`) → empty set
- payload with no calc instances → empty set
- multiple tasks → union of all their rows
- `pk is None` instance → excluded

**B. startup sweep — E2E with a real CalculationModel**
- tracked row → stays `IN_PROGRESS`, **no audit written**
- untracked row → `ABORTED` + audit written
- mixed rows (one tracked, one not) → only the untracked aborts
- recovery-off (empty tracked set) → all rows abort (back-compat)
- `CALLED_FROM_START_COMMAND` unset → no-op
- precomputed `tracked_record_ids` passed in is honored (no extra registry read)

Each test imports the changed source (coverage pairing). Plan files synced at
Step 7 (test-clusters, dashboard, test-writing-plan, session-log).

## Out of scope

- Rewiring the abort path to *itself* requeue (the supervisor already does this).
- Touching the `asyncio` startup wrapper.
- Any change to PR #603's terminal guard (this design is what makes #603 and the
  startup sweep coexist correctly).

## Reliability summary

After this change, on **any** pod restart:

- Live worker pod → its rows are left alone; it finishes them.
- Dead worker pod → its rows are left tracked; the recovery supervisor requeues
  and resumes them (state preserved, result still delivered to any waiter).
- Truly unrecoverable rows (nothing tracks them) → `ABORTED`, the honest
  terminal, with a full audit trail.

No recoverable calculation is ever aborted out from under the machinery that
would have resumed it.
