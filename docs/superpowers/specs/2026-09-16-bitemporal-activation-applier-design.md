# Bitemporal activation: the in-database applier

**Status:** design, awaiting review
**Date:** 2026-09-16 (rewritten the same day; see §1)
**Linear:** [LEX-131](https://linear.app/lundgroup/issue/LEX-131) (parent) · LEX-135 · LEX-136 · LEX-578 · LEX-593
**Related PR:** [#695](https://github.com/ExcellenceCloudGmbH/lex-app/pull/695) — the reconcile floor (open)

---

## 1. What this document decides

LEX-135 settled *where* future-dated bitemporal activation runs: inside the instance
database, driven by pg_cron. That is verified on dev and **not reopened here**.

This document decides three things LEX-135 leaves open:

1. **What the applier may skip.** LEX-135's fourth checkbox asks whether
   `activate_history_version` has app-level side effects that raw SQL would bypass. §2.3
   answers it from the source: it has none that run.
2. **How the applier discovers its work.** Postgres cannot call `apps.get_models()`. §4.
3. **What the applier must replicate exactly.** §5.

A first draft of this document, written earlier the same day, was organised around a
propagation side effect — dependent calculated models recalculating at activation. On
inspection that code path is dead: the method it depends on is defined nowhere in v2. The
draft's central trade-off therefore did not exist, and this rewrite replaces it. The
finding itself is real and is recorded in §10.1 and §12.6 as a separate matter.

---

## 2. What exists today

Read from the source on `lex-app-v2`, not assumed. Where a claim depends on what *runs*
rather than what is *wired*, the distinction is stated.

### 2.1 Scheduling

`lex/core/services/bitemporal_signals.py::_schedule_future_activation` branches on one
environment variable:

```python
if os.getenv("CELERY_ACTIVE", "false").lower() != "true":
    LocalSchedulerBackend().schedule(run_at_time=history_instance.valid_from, ...)
else:
    PeriodicTask.objects.create(clocked=ClockedSchedule(...), one_off=True, ...)
```

`LocalSchedulerBackend` (`lex/process_admin/utils/local_scheduler.py`) is a singleton
around `sched.scheduler` on a daemon thread. The queue is process memory. Nothing persists
and nothing rehydrates it, so a restart, redeploy, node move or OOM silently drops every
pending activation on that instance — the meta row stays `SCHEDULED` forever, nothing is
logged.

**That branch serves 47 of 53 production instances.**

### 2.2 Activation

`lex/lex_app/celery_tasks.py::activate_history_version` resolves the model, re-checks
`valid_from` against now (5 s tolerance), calls `BitemporalSynchronizer.sync_record_for_model`,
then flips meta rows from `SCHEDULED` to `DONE`. Failure is reported by **return value**
(`"failed_model_lookup"`, `"skipped_missing_record"`, `"failed_too_early"`, `"success"`),
not by raising.

`sync_record_for_model` (`lex/process_admin/utils/bitemporal_sync.py`) is **convergent**.
Inside one `transaction.atomic()`:

```python
effective = (history_model.objects.select_for_update()
    .filter(**{pk_name: pk_val})
    .filter(valid_from__lte=now)
    .filter(Q(valid_to__gt=now) | Q(valid_to__isnull=True))
    .order_by("-valid_from", "-history_id").first())
```

then upserts the main row from the effective row's fields, or deletes the main row when
`history_type == "-"`. It computes the end state; it never replays a sequence. Two runs
produce one result.

### 2.3 What `.save()` does at activation — the full inventory

The upsert ends at:

```python
main_instance.skip_history_when_saving = True
main_instance.save(skip_hooks=True)
```

`skip_hooks` routes to `base_save = super(LifecycleModelMixin, self).save` — Django's
`Model.save`, which emits `post_save` unconditionally. So the question is not whether
signals fire (they do) but whether any receiver does anything. Every candidate:

| Candidate side effect | Runs at activation? | Why not |
|---|---|---|
| Dependent recalculation (`do_post_save` → `CalculatedModelUpdateHandler.register_save`) | **No — dead code** | `register_save` returns on its first line: `if not hasattr(updated_entry, 'get_dependent_entries'): return`. That method is defined nowhere in the framework, nowhere in the example project, and not provided dynamically. The mixin that supplied it in v1 (`DependencyAnalysisMixin`) is commented out with *"was not migrated from old structure"*. |
| Process-flow `update_handler` (`CreateOrUpdate.py:38`) | **No — broken** | `post_save.connect(update_handler)` is passed the *module* `lex.core.calculated_updates.update_handler`, not a callable. It raises on connect (the frontend's BUG-F-013). |
| Audit log (`AuditLogMixin`) | **No — never on this path** | Mixed into the DRF views `One.py` / `Many.py`; its surface is `perform_create` / `perform_update` / `perform_destroy`. Activation is not a request. |
| WebSocket `record_mutation` push | **No — never on this path** | `broadcast_model_mutation` is called only from `One.py` and `Many.py`. Not connected to any signal, not called from `sync_record_for_model` or `LexModel.save`. |
| `edited_at` / `edited_by` stamping | Suppressed | `_should_skip_edited_fields_update` returns `True` under `skip_history_when_saving`; and it is a lifecycle hook, which `skip_hooks` bypasses anyway. |
| History row (simple-history) | Suppressed | `skip_history_when_saving = True`. |
| django-lifecycle `@hook`s | Suppressed | `skip_hooks=True`. |
| Caches over main-table data | None exist | — |

**Conclusion: activation on v2 is a pure data write.** `sync_record_for_model` does exactly
two things — converge the main row, and flip meta status. A pl/pgSQL applier that does the
same two things bypasses nothing that runs. LEX-135's fourth checkbox is resolved.

Two consequences worth stating because they surprise:

- A list view showing a record does **not** refresh when that record activates, today. It
  never has. This design neither fixes nor worsens that (§10.5).
- Dependent calculated models have **not** refreshed on *any* save since the v2 migration,
  not only on activation. That is a calculation-engine finding, not a scheduling one, and
  is handed off in §12.6.

### 2.4 The meta model is versioned, and activation bypasses that

The meta model (`MetaLevelHistoricalRecords`, `lex/core/services/MetaHistory.py`) is
itself bitemporal in system time: `sys_from` / `sys_to`, `meta_history_id`, and a
`chain_sys_to` handler on its `post_save` that closes the previous version. Its physical
table is `<app>_<model>_meta_history`.

Activation does **not** create a new meta version. It mutates in place:

```python
MetaModel.objects.filter(history_object_id=history_id, meta_task_status="SCHEDULED") \
    .update(meta_task_status="DONE")
```

A queryset `.update()` emits no signal, so `chain_sys_to` never runs and no version is
opened or closed. It also touches **every** `SCHEDULED` meta version for that
`history_object_id`, not only the current one (`sys_to IS NULL`).

Whether that is correct is not this document's question. What matters here is that the
applier must **mirror it exactly** — an in-place `UPDATE` with the same predicate — so the
two paths remain byte-equivalent. Improving the meta flip is a separate change and must be
made to both paths at once.

### 2.5 How the floor finds its work

`lex/core/services/activation_reconcile.py` (PR #695) discovers bitemporal models at
runtime:

```python
def iter_bitemporal_models():
    for model in apps.get_models(): ...
```

then, per `(main, history, meta)` triple, selects meta rows with
`meta_task_status='SCHEDULED'` whose history row has `valid_from <= now` and
`valid_from >= now - max_age`. No queue table. The meta row is its source of truth.

This is the fact that shapes §4: **the floor's discovery mechanism is Python, and Postgres
cannot run it.**

---

## 3. The settled decision (restated, not reopened)

From LEX-135: future-dated activations are applied by a per-database pg_cron job running
a pl/pgSQL function inside Cloud SQL — no scheduler pod, works with every backend scaled
to zero, and the reconcile loop from #695 stays as the safety floor. Verified on dev:
lex-main-2, pg_cron 1.6.7, cross-database jobs firing every 30 s, stale-fire guards
no-opping cleanly (`UPDATE 0`).

From LEX-136, the constraints the applier inherits:

- Function name and cadence: `cron.schedule_in_database('apply-activations-<db>',
  '* * * * *', 'SELECT lex_apply_due_activations()', '<db>')` — **every minute**.
- Registration is done **once per instance database by the instance controller** at
  provisioning (it already runs as `initdeploy` to create databases); deregistration on
  deletion mirrors it.
- **The app's own database user gets no cron access and no `postgres`-database access.**
  It may only read and write tables in its own database.

The last point decides where things live: the function and everything it reads must be
created by lex-app's own migrations, in the instance database, under the app role.

**"Backend agnostic" means backend-*server* agnostic** — no Django process, no Celery, no
broker. It does not mean database-agnostic; this binds activation to PostgreSQL with
pg_cron. §8.13 covers what that costs.

---

## 4. Discovery: how the applier knows what to apply

### 4.1 The problem

`lex_apply_due_activations()` needs, for every bitemporal model, the names of three tables
and the primary-key column. The floor gets them from Django's app registry (§2.5).
pl/pgSQL has no equivalent. Discovery must be **materialised in the database**.

### 4.2 The two honest shapes

**Per-activation queue table** — LEX-135's wording. At save time lex-app writes one row per
future-dated activation, naming its tables and `history_id`. The applier scans one table.

- For: single indexed scan; framework-owned, so attempt counters and max-age have a natural
  home; verified on dev in this shape.
- Against: a **second source of truth** beside `meta_task_status`. Both are written in the
  user's transaction, so they agree at write time — but the applier reads the queue and the
  floor reads meta rows, so the two mechanisms consult different tables, and parity between
  them becomes something to prove rather than something that follows.

**Per-model registry + meta rows.** lex-app writes one row per bitemporal model naming
its `(main, history, meta, pk_column)`. The applier iterates the registry and runs **the
floor's own predicate** against each meta table.

- For: **one source of truth** — the `SCHEDULED` meta row and its history row's
  `valid_from`, read identically by both paths. Parity is near-automatic. No per-activation
  write at save time; `_schedule_future_activation` changes only in what it stops doing.
- Against: N scans per tick instead of one, so the `meta_task_status` index (LEX-578) is
  load-bearing for both paths. Attempt counters have no framework-owned per-activation home.

LEX-135's stated rationale for the queue table — *same transaction as the user's save, so
cancel/supersede is local* — is equally true of the meta row, which is written in that same
transaction. The rationale does not distinguish the two shapes.

### 4.3 Decision

**Registry + meta rows.** One source of truth.

The argument that decides it: this design has two activators by construction, forever. The
single most valuable property they can have is that they cannot disagree. Reading the same
row through the same predicate gives that for free; reading different tables means every
future change must be made twice and tested for equivalence. The cost — N small scans per
minute — is bought back by the index in §5.5, which the floor needed regardless.

A third option, making the queue table the *only* truth and having the floor read it too,
was considered and rejected: it changes #695 before it merges, and it demotes
`meta_task_status` from the record of intent the reconcile design was built on to an
informational field.

---

## 5. Design

### 5.1 Architecture

```
       save — Django, in the user's transaction         apply — Postgres, every minute
       ────────────────────────────────────────         ──────────────────────────────────
 user save ──► history row   valid_from = future        pg_cron ──► lex_apply_due_activations()
           ──► meta row      meta_task_status='SCHEDULED'          │ for each registry row:
                                                                    │   meta rows SCHEDULED
                                                                    │     whose history.valid_from <= now()
                                                                    │   converge main row  (upsert | delete)
                                                                    │   UPDATE meta SET status='DONE'
                                                                    ▼
                               ┌────────── same predicate, same writes ──────────┐
                               │                                                  │
                    floor — #695, Python, 60 s                        applier — pl/pgSQL
                    discovers via apps.get_models()                   discovers via lex_bitemporal_registry
```

Neither path knows about the other. Both converge to the same end state. The registry
exists for one reason: Postgres cannot call `apps.get_models()`.

### 5.2 `lex_bitemporal_registry`

A framework-owned table, created by a lex-app migration:

| Column | Meaning |
|---|---|
| `app_label`, `model_name` | Django identity, for logging and for the floor to cross-check |
| `main_table`, `history_table`, `meta_table` | Physical names — `<app>_<model>`, `<app>_historical<model>`, `<app>_<model>_meta_history` |
| `pk_column` | `model._meta.pk.name` — any single-column pk, not assumed to be `id` |
| `registered_at` | Last upsert |

Unique on `(main_table)`. One row per bitemporal model.

### 5.3 `lex_apply_due_activations()`

pl/pgSQL, created by a lex-app migration (`RunSQL`) so it lives in the instance database
under the app role (§3). All table references are dynamic — `EXECUTE format(%I ...)` —
because the function is generic over customer models it has never seen.

For each registry row, for each distinct `pk` with a due `SCHEDULED` meta row:

1. **Select the effective history row, locked:**
   `... WHERE pk = $1 AND valid_from <= now() AND (valid_to > now() OR valid_to IS NULL)
   ORDER BY valid_from DESC, history_id DESC LIMIT 1 FOR UPDATE`
   — the predicate from §2.2, evaluated at apply time. If it is not the row that was
   scheduled, that is a supersede and step 2 still applies the *effective* one; if there is
   no effective row, nothing is written.
2. **Converge the main row.**
   - `history_type = '-'` → `DELETE FROM main WHERE pk = $1`.
   - otherwise → `INSERT INTO main (cols) SELECT cols FROM history WHERE history_id = $2
     ON CONFLICT (pk) DO UPDATE SET col = EXCLUDED.col, ...` — where `cols` is
     `columns(main) ∩ columns(history)` read from `information_schema.columns` **at apply
     time**. No exclusion list is hardcoded: the history table's own columns
     (`history_id`, `valid_from`, `valid_to`, `history_type`, `history_change_reason`,
     `history_user_id`) fall out because the main table does not have them.
3. **Flip meta, in place, mirroring §2.4 exactly:**
   `UPDATE meta SET meta_task_status='DONE' WHERE history_object_id = $2 AND
   meta_task_status='SCHEDULED'`.
4. Steps 1–3 run inside a per-`pk` `BEGIN ... EXCEPTION WHEN OTHERS THEN ... END` block,
   so one failing record raises a `WARNING` (visible in `cron.job_run_details`) and the
   loop continues. The main-row write and the meta flip commit together or not at all.

Bounded by **max-age**: rows whose `valid_from` is older than the configured window are
skipped and counted, never applied — so an instance restored from an old backup cannot
replay a year of history on its first tick. The value mirrors
`LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS` and lives in a one-row settings table the migration
seeds, since pl/pgSQL cannot read environment variables.

### 5.4 The floor (#695)

Predicate and writes: **unchanged**. Two additions:

- On its startup pass, it **populates the registry first** (§5.6), then proceeds as today.
- It reads its model list **from the registry** rather than `apps.get_models()` directly, so
  the two paths cannot disagree about which tables exist. (`apps.get_models()` is still
  what writes the registry; the floor consumes what it wrote.)

It remains the only activator wherever pg_cron is absent (§8.13), which is why §4.3's
rejected third option — demoting it to detect-only — would have reintroduced the failure
this project exists to remove.

### 5.5 The index

Both paths now select on `meta_task_status`, every minute, per meta table, forever. LEX-578
records the floor's scan as "free on small instances, unmeasured on a large one"; this
design doubles it and makes it permanent.

The direct fix — `db_index=True` on the generated meta model — forces a migration into
**every customer repository**, because the meta model is generated per customer model.
LEX-578 flags exactly that.

Instead: a **partial index** on each registered meta table, created at registration time
(§5.6) with `CREATE INDEX IF NOT EXISTS ... ON <meta_table> (history_object_id) WHERE
meta_task_status = 'SCHEDULED'`. `SCHEDULED` rows are transient and rare, so the index is
tiny, and it serves both the applier's scan and the floor's. Trade, stated: framework DDL
against generated tables at startup. It is idempotent and confined to tables the framework
itself generated.

### 5.6 Registry population

An **idempotent upsert at served-backend startup**, in `lex/lex_app/apps.py` next to
`start_background_reconcile()`, under the same `running_in_uvicorn()` gate and the same
try/except that logs and continues. Iterates `apps.get_models()`, upserts one registry row
per bitemporal triple, creates the partial index (§5.5), and removes registry rows for
models no longer present.

Why startup rather than a migration: a data migration would see only the models present
when it ran; a model added afterwards would never register. Startup re-derives the set on
every boot. Why the uvicorn gate: `ready()` runs in every process, including management
commands before migrations exist; the served backend is the one process known to have a
migrated database.

The apparent gap — a model added but the backend not yet restarted — does not exist: a
model cannot have a future-dated history row until the application that defines it has
run.

### 5.7 Two sub-decisions

**No attempt-counter table in v1.** Without a queue table there is no per-activation home
for one, and a `lex_activation_attempts` table would be a queue table under another name —
the dual-truth cost §4.3 rejected. The applier is bounded instead by max-age (§5.3) and by
isolation: a poison row costs one failed statement per tick, is visible in
`cron.job_run_details`, and blocks nothing. #695's in-memory counter continues to bound the
floor. Add the table only if a real poison row is ever observed; §12 lists it.

**Execution role is LEX-136's decision, not this document's.** The function must run as a
role that can write the app's tables. Two workable shapes: register the job with the app
role as `username`, or make the function `SECURITY DEFINER` owned by the app role. The
requirement is stated here; the choice belongs with the permissions work.

---

## 6. The three-model contract (LEX-593)

| Model | Written when | By |
|---|---|---|
| History | Save time | Django, user's transaction |
| Meta | Save time (`SCHEDULED`); apply time (`DONE`) | Django, then the applier |
| Main | Apply time | The applier |

Acceptance for LEX-593: with **no backend process running at all**, a future-dated save
ends with all three tables in their final, mutually consistent state — including the
supersede case, where the main row reflects the superseding change and the superseded
history row's meta status is also `DONE`.

---

## 7. What changes, and who notices

| | Before | After |
|---|---|---|
| Non-Celery instances (47) | Timer lost on restart; activation never happens | Applied from the database; survives restarts and scale-to-zero |
| Celery instances (6) | Beat fires a task | Applier fires; beat scheduling retired for activations |
| Dependent calculations | Not recalculated (dead since v2, §2.3) | Unchanged — now explicit and tested |
| UI list views | Not notified of activation | Unchanged (§10.5) |
| Activation cost | One main-row write on the firing thread | Bounded SQL in the database |
| New schema | — | `lex_bitemporal_registry`, one settings row, one partial index per meta table |
| Local dev / SQLite | Local scheduler | Reconcile floor only (§8.13) |

---

## 8. Edge cases

**8.1 Supersede.** A later save inserts a history row that is effective before the queued
one fires. The applier re-derives the effective row at apply time and converges to it; the
earlier row's meta status still flips to `DONE` because it is no longer pending.

**8.2 Cancel by deletion.** The history row is deleted before activation. Today
`pre_delete → cancel_schedules` removes the timer. Under this design there is no timer to
cancel; the meta row is what must be handled — either deleted with its history row, or
left `SCHEDULED` pointing at a missing row, which the applier must treat as a no-op rather
than an error. The existing `repair_chain` on `post_delete` is the place to settle it.

**8.3 Several rows becoming due at once.** After downtime, several history rows for one
`pk` may be past due. A loop applying each in turn writes intermediate states to the main
table. The applier must converge — effective row only — exactly as `sync_record_for_model`
does. **This is the most likely implementation mistake.**

**8.4 Deletion as the effective record.** `history_type = '-'` means the main row is
removed. An applier written only for the upsert branch leaves deleted records alive.

**8.5 Ordering across pks.** Convergence is per `(model, pk)`. Across records there is no
ordering guarantee and none is required.

**8.6 Schema drift.** Future-dated changes can be months out; a migration in between would
invalidate any column list fixed at save time. The applier derives columns at apply time
(§5.3). This is the constraint that made LEX-135's "precomputes the activation outcome as
guarded SQL" unworkable as literally worded: what is precomputed is the intent, never the
statement.

**8.7 Timezone.** `valid_from` is `timestamp with time zone` (`USE_TZ = True`). pl/pgSQL
`now()` is the same type; the comparison is exact. The floor compares in Python and must
stay explicitly UTC. This subsystem has had two timezone fixes (#635, #638); it needs a
test on both paths, not care.

**8.8 Clock source.** The applier and `valid_from` share the database clock, which removes
the app-vs-database skew that `activate_history_version`'s 5-second tolerance exists to
absorb. The floor keeps the tolerance.

**8.9 Double application.** Applier and floor may both act on one row. Safe because both
converge to the same state and the second sees `DONE`. Must be asserted, not assumed: it is
the property that makes running the floor forever free.

**8.10 Poison row.** One record fails every tick. Isolated per-`pk` (§5.3); no counter in
v1 (§5.7). The failure is visible in `cron.job_run_details` and via the applier's
`WARNING`s.

**8.11 Very old due rows.** Bounded by max-age on both paths. Below the bound, skipped and
counted, never applied.

**8.12 Meta versioning.** The applier mirrors the in-place `UPDATE` (§2.4), touching every
`SCHEDULED` version for the `history_object_id`. It does not open a new meta version. Any
future improvement to that must land on both paths together.

**8.13 No pg_cron.** Local development, CI, SQLite, self-hosted PostgreSQL without the
extension, non-PostgreSQL backends: no applier. The floor is the only mechanism. **Test
environments therefore exercise the floor, not the applier**; the applier needs its own
integration coverage against real PostgreSQL with pg_cron or it ships untested.

**8.14 Cross-database registration and role.** LEX-136's subject. Not resolved here; the
requirement on the execution role is in §5.7.

**8.15 Sequential scan.** Both paths select on `meta_task_status` every minute. §5.5.

**8.16 Row locking.** A concurrent user save and an activation can touch one main row.
`sync_record_for_model` takes `select_for_update` on both the history row and the main
row; the applier takes `FOR UPDATE` on the history row and relies on the `INSERT ... ON
CONFLICT` row lock for the main row. Deadlock ordering (history before main) must match
the Python path.

**8.17 Registry drift.** A model removed from the codebase leaves a registry row pointing at
tables that may be dropped. Startup population removes rows for absent models (§5.6); if a
tick runs in between, the per-`pk` exception block skips it with a `WARNING`.

**8.18 Non-`id` primary key.** `sync_record_for_model` uses `model._meta.pk.name`. The
registry carries `pk_column` and the applier uses it in every predicate and in the `ON
CONFLICT` target. Composite keys are not supported by the existing code and are not
supported here.

**8.19 A customer model defining `get_dependent_entries`.** Nothing in the framework
provides it (§2.3), but a customer repository could. That model would then propagate on
the floor and not on the applier. The parity test in §13 catches it; the fix is to suppress
`post_save` propagation in the activation path unconditionally rather than rely on the
method's absence.

---

## 9. Failure modes

| Failure | Detected by | Cost |
|---|---|---|
| pg_cron job never registered | Floor applies it within its interval | Latency |
| pg_cron extension absent | Same | Same |
| Applier raises on one row | Per-`pk` exception block; `WARNING` in `cron.job_run_details` | That record stays pending; others unaffected |
| Backend never runs (scale to zero) | — | None; this is the design's purpose |
| Applier and floor both disabled | Nothing | Silent non-activation — the state being left |
| Schema drift mid-flight | Apply-time column derivation | None |
| Registry row for a dropped table | Exception block; removed on next startup | That model skipped until restart |
| Meta row whose history row was deleted | No effective row → no write | None |

---

## 10. Explicitly out of scope

**10.1 Restoring dependency propagation.** Dead since the v2 migration (§2.3), for every
save on every model. Whether to restore it is a v1-parity question about the calculation
engine, larger than activation and with its own customers and risks. This design neither
depends on the answer nor forecloses it. Its own issue: §12.6.

**10.2 Calculation scheduling.** Different properties — long-running, resource-hungry,
needs a worker. Nothing here assumes it wants the same mechanism.

**10.3 How schedules are created.** `_schedule_future_activation` keeps writing the meta
record. It stops creating timers and `PeriodicTask` rows; nothing else changes.

**10.4 Migrating existing `PeriodicTask` rows.** They stop being needed; the meta rows
already carry the intent.

**10.5 Notifying the UI.** Activation has never pushed a `record_mutation` (§2.3), so an
open list view goes stale until refresh — today and after this change. Fixing it from
inside Postgres would need `NOTIFY` and a backend listener, which is new machinery for a
pre-existing gap. Named so it is not mistaken for a regression; §12.7.

---

## 11. What this design deliberately does not do

- It does not add a per-activation table (§4.3).
- It does not change the meta flip's semantics (§2.4, §8.12).
- It does not change the floor's predicate or writes (§5.4).
- It does not suppress `post_save` on the activation path, because nothing that runs
  depends on it (§2.3) — but §8.19 records when that would change.

---

## 12. Open items for someone else

1. **LEX-136** — execution role for the function (§5.7) and job registration.
2. **LEX-578** — measure the scan on the largest instance; this design makes the index
   load-bearing for both paths and proposes the partial-index shape (§5.5).
3. **PR #695 merge** — the floor is this design's prerequisite.
4. **Instances with `autoscaling: true`** — the applier serves them and the floor cannot.
5. **Attempt-counter table** — only if a real poison row is observed (§5.7).
6. **Dead dependency propagation, framework-wide** — `DependencyAnalysisMixin` was not
   ported to v2, so `CalculatedModelUpdateHandler.register_save` has returned on its first
   line since the migration, for every save on every model. Not caused or fixed here.
   Needs an issue against the calculation engine, and confirmation against a v1 instance
   that it *did* propagate before this is filed as a regression.
7. **UI notification on activation** — pre-existing gap (§10.5).

---

## 13. How it gets tested

Cluster 05-history, following batch 5n (5.104–5.109, the floor).

- **Producer:** a future-dated save writes a `SCHEDULED` meta row and no timer; rolling
  back leaves nothing.
- **Registry:** startup registers every bitemporal triple with the right `pk_column`;
  removing a model removes its row; re-running is idempotent.
- **Supersede / cancel / convergence / deletion:** §8.1–8.4, each against the applier.
- **Parity — the one that matters most:** applier and floor, run against identical
  fixtures, produce identical `(main, history, meta)` state. This is the test that makes
  §4.3's argument true in practice.
- **No propagation:** activation through either path leaves other tables untouched.
- **Meta mirror:** the applier's flip touches exactly the rows the Python `.update()`
  touches, and opens no new meta version (§8.12).
- **Timezone:** a `valid_from` either side of a UTC-offset boundary activates at the right
  instant on both paths (§8.7).
- **Idempotence:** applier then floor, and floor then applier, converge (§8.9).
- **Isolation:** one failing `pk` does not prevent the others from applying (§8.10).

The applier's tests need real PostgreSQL with pg_cron (§8.13); the floor's do not.
