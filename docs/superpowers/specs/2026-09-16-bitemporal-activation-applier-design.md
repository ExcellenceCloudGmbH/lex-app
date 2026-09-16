# Bitemporal activation: the in-database applier

**Status:** design, revision 2, awaiting review
**Date:** 2026-09-16 (rewritten and then revised the same day; see §1)
**Linear:** [LEX-131](https://linear.app/lundgroup/issue/LEX-131) (parent) · LEX-135 · LEX-136 · LEX-578 · LEX-593
**Related PR:** [#695](https://github.com/ExcellenceCloudGmbH/lex-app/pull/695) — the reconcile floor (open)

---

## 1. What this document decides

LEX-135 settled *where* future-dated bitemporal activation runs: inside the instance
database, driven by pg_cron. That is verified on dev and **not reopened here**.

This document decides four things LEX-135 leaves open:

1. **What the applier may skip.** LEX-135's fourth checkbox asks whether
   `activate_history_version` has app-level side effects that raw SQL would bypass. §2.3
   answers it from the source: it has none that run.
2. **Where the boundary between lex-app and the database lies.** What lex-app may do at
   deploy time and at write time, and what it may never do at apply time. §3.2.
3. **How the applier discovers its work.** Postgres cannot call `apps.get_models()`. §4.
4. **What the applier must replicate exactly.** §5.

**Revision history, same day.** A first draft was organised around a propagation side
effect — dependent calculated models recalculating at activation. That code path is dead:
the method it depends on is defined nowhere in v2 (§2.3). The finding is recorded in §10.1
and §12.6 as a separate matter.

The second version (revision 1) discovered bitemporal tables through a registry table that
a served backend populated at startup. That put a lex-app process back into the apply path —
the applier knew only what the last backend start had told it — which is the dependency this
design exists to remove (§3.2). Revision 2 derives discovery from the database catalog
(§4.3, §5.2), drops the registry, its startup hook and the settings table, turns the max-age
skip into an alert (§5.3, §8.11), adds the full unavailability matrix (§9) and a worked
example (§14).

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

### 3.1 Who computes what

The idea behind pg_cron, stated plainly: the backend works out what a record should look
like, from when, and which records change; the database applies it. lex already does the
first half today. Each phrase maps to one concrete piece:

| The idea | What it is in lex | Who does it, when |
|---|---|---|
| What it should look like | The history row. Every row is a full snapshot of the record as it must appear from its start time | Backend, at save time |
| At what time | `valid_from` on that history row | Backend, at save time |
| Which records change | The meta row marked `SCHEDULED`, one per future history row | Backend, same transaction |
| The change applies | One standing pg_cron job calls one function, which copies the snapshot into the main table — or deletes the row — and marks the meta row `DONE` | Database, every tick |

The backend computes everything about the domain; the database computes nothing about it.
The function does not know what a fee or a report is. It knows three table shapes and a
timestamp.

Two refinements over a literal "replay the stored change":

- **No separate change-list table.** The history row and its meta row already *are* the
  precomputed change. A second copy would have to be updated on every edit, cancel or delete
  that touches a future row, and the moment one path is missed the two disagree (§4.3).
- **Effective row, not stored row.** When the applier runs late and several changes for one
  record are due at once, it applies the row that is effective *now*, not each stored change
  in turn (§2.2, §8.3). Same end state, fewer writes, no transient states visible to users.

The shape is forced by LEX-136: the app role has no cron access, so the backend cannot
schedule one job per change at its exact time. The controller registers one standing job per
database, and the backend can only leave data for it to find. LEX-135 verified the job at a
30-second interval; the minute above is a knob, not a limit.

### 3.2 The boundary

lex-app is involved at exactly two moments, and forbidden at a third:

| Moment | lex-app's part | Allowed? |
|---|---|---|
| **Deploy time** | Migrations create the three tables, declare the index (§5.5) and install the SQL functions (§5.3, §5.6) | Yes — the tables have to come from somewhere, and the functions must version with lex-app |
| **Write time** | The user's save writes the history row and the `SCHEDULED` meta row (§10.3) | Yes — someone has to record the intent, and there is no separate schedule store to drift from it |
| **Apply time** | Nothing. No process, no registration, no cache, no timer | **Never.** Anything the apply path needs must come from the database itself |

The floor (#695) runs in lex-app and is therefore not part of the apply path's contract. It
is the fallback for environments without pg_cron (§8.13). Production correctness must not
depend on it.

Revision 1 failed this test in one place: a registry table that the served backend populated
at startup. The applier then knew only the models a backend had registered since the last
deploy — wrong for a database restored from backup, a deploy that fails after migrate, a
row written by a data migration or a script, or a renamed table. The registry's content was
also derivable from the catalog (§5.2). It is gone.

**Acceptance for the boundary** is §6's acceptance: stop every lex-app process, let a due
row pass its `valid_from`, and observe all three tables in their final state.

---

## 4. Discovery: how the applier knows what to apply

### 4.1 The problem

`lex_apply_due_activations()` needs, for every bitemporal model, the names of three tables
and the primary-key column. The floor gets them from Django's app registry (§2.5). pl/pgSQL
has no equivalent. Discovery must happen **inside the database**, from what the database
already holds or from what lex-app has written into it — and, per §3.2, never from a
process that has to be running.

### 4.2 The three honest shapes

**Per-activation queue table** — LEX-135's wording. At save time lex-app writes one row per
future-dated activation, naming its tables and `history_id`. The applier scans one table.

- For: single indexed scan; framework-owned, so attempt counters have a natural home;
  verified on dev in this shape.
- Against: a **second source of truth** beside `meta_task_status`. Both are written in the
  user's transaction, so they agree at write time — but the applier reads the queue and the
  floor reads meta rows, so the two mechanisms consult different tables, and parity between
  them becomes something to prove rather than something that follows. It also carries either
  a *payload*, which drifts under schema change (§8.6), or a pointer to the history row, which
  makes the queue row a duplicate of the meta row.

**Per-model registry + meta rows** — revision 1's choice. lex-app writes one row per
bitemporal model naming its `(main, history, meta, pk_column)`, upserted at served-backend
startup. The applier iterates the registry and runs **the floor's own predicate** against
each meta table.

- For: **one source of truth for what is pending** — the `SCHEDULED` meta row and its history
  row's `valid_from`, read identically by both paths. No per-activation write at save time;
  `_schedule_future_activation` changes only in what it stops doing.
- Against: a **second source of truth for which tables exist**, and one that a lex-app
  process must refresh. Stale after a restore, a failed deploy or a rename; empty on a
  database no served backend has started against. Revision 1 argued that "a model cannot
  have a future-dated row before its application has run" — true of the first row, false of
  everything after it: a data migration, a script, a restored dump. It fails §3.2.

**Catalog derivation + meta rows.** Nothing is written for discovery at all. The three
tables are named from one another by lex's own conventions, and the PostgreSQL catalog
already lists them. The applier derives the triples on every tick (§5.2).

- For: cannot be stale — the catalog *is* the tables. Nothing to populate, no startup hook,
  no lex-app involvement at apply time. The same single truth for what is pending as the
  registry shape.
- Against: depends on the naming conventions holding — a bitemporal model with a custom
  `db_table` would not be found (§8.21) — and on quoting identifiers correctly (§8.20). A
  handful of catalog queries per tick (§12.9).

LEX-135's stated rationale for the queue table — *same transaction as the user's save, so
cancel/supersede is local* — is equally true of the meta row, which is written in that same
transaction. The rationale does not distinguish the shapes.

### 4.3 Decision

**Catalog derivation + meta rows.** One source of truth for what is pending, and no source
at all for what exists other than the catalog itself.

The argument that decides it: this design has two activators by construction, forever. The
single most valuable property they can have is that they cannot disagree. Reading the same
meta row through the same predicate gives that for *what is pending*. For *which tables
exist*, the floor asks Django and the applier asks the catalog; they can disagree only if a
model exists in one and not the other, and the parity test (§13) asserts they do not.

**The proof that the registry was unnecessary.** On a real project database with 51
bitemporal models, the rules in §5.2 recovered every triple from the catalog alone. A first
attempt through `django_content_type` did not — dynamically registered models have no
content-type rows — which is why the rules use table shape and names, not Django's tables:

| Discovery method | Triples found |
|---|---|
| `django_content_type` | 7 of 51 |
| Catalog shape and naming rules (§5.2) | 51 of 51 |

A third option, making the queue table the *only* truth and having the floor read it too,
was considered and rejected: it changes #695 before it merges, and it demotes
`meta_task_status` from the record of intent the reconcile design was built on to an
informational field.

---

## 5. Design

### 5.1 Architecture

```
       save — Django, in the user's transaction         apply — Postgres, every tick
       ────────────────────────────────────────         ──────────────────────────────────
 user save ──► history row   valid_from = future        pg_cron ──► lex_apply_due_activations()
           ──► meta row      meta_task_status='SCHEDULED'          │ for each triple in lex_bitemporal_tables():
                                                                    │   meta rows SCHEDULED
                                                                    │     whose history.valid_from <= now()
                                                                    │   converge main row  (upsert | delete)
                                                                    │   UPDATE meta SET status='DONE'
                                                                    ▼
                               ┌────────── same predicate, same writes ──────────┐
                               │                                                  │
                    floor — #695, Python, 60 s                        applier — pl/pgSQL
                    discovers via apps.get_models()                   discovers via the catalog (§5.2)
```

Neither path knows about the other. Both converge to the same end state. Nothing in lex-app
runs at apply time (§3.2).

Three functions, one SQL file, one migration (§5.7):

| Function | Role |
|---|---|
| `lex_bitemporal_tables()` | Discovery (§5.2). Returns `(main_table, history_table, meta_table, pk_column)` per model |
| `lex_pending_activations()` | Visibility (§5.6). Every `SCHEDULED` row across every triple, due or not |
| `lex_apply_due_activations()` | The applier (§5.3) |

### 5.2 `lex_bitemporal_tables()` — discovery from the catalog

Four rules, applied on every call, all against `information_schema` and `pg_catalog`:

1. **Meta tables by shape.** A table is a meta table if it has all of `meta_task_status`,
   `history_object_id`, `sys_from` and `sys_to`. The shape is fixed by
   `MetaLevelHistoricalRecords` (§2.4) and no other lex table has it.
2. **Main table by suffix.** lex names the meta table after the main table:
   `<main>_meta_history` (`lex/lex_app/migrations/0001_initial.py` shows
   `lex_app_asoftestmodel_meta_history` beside `lex_app_asoftestmodel`). Strip the suffix.
3. **History table by the last underscore.** simple-history's default is
   `<app_label>_historical<modelname>`. Model names are class names lowercased and never
   contain an underscore; app labels may (`lex_app`). So the *last* underscore in the main
   table's name splits app from model unambiguously, and `historical` is inserted there.
4. **Keep the triple only if all three tables exist** — `to_regclass(format('%I', name))`
   on each — and the main table has a single-column primary key, read from `pg_index`.
   Anything else is skipped with a `NOTICE` naming the table (§8.17, §8.18, §8.21).

Identifiers always pass through `%I`. Real project app labels contain capitals, so the
physical names are case-sensitive quoted identifiers; unquoted, the existence check would
silently report them missing (§8.20).

Why not `django_content_type`: it has rows only for models Django's migration machinery has
seen. On the project database in §4.3 that was 7 of 51. The catalog has all 51 because the
tables exist, whatever created them.

Cost: a few catalog queries per tick. Not measured at scale and not cached in v1 (§12.9).

### 5.3 `lex_apply_due_activations()`

pl/pgSQL, created by a lex-app migration (`RunSQL`) so it lives in the instance database
under the app role (§3). All table references are dynamic — `EXECUTE format(%I ...)` —
because the function is generic over customer models it has never seen.

For each triple from `lex_bitemporal_tables()` (§5.2), for each distinct `pk` that has a
`SCHEDULED` meta row — found through the partial index (§5.5) — whose history row has
`valid_from <= now()`:

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

**Not bounded by age.** Revision 1 skipped rows whose `valid_from` was older than a window,
so that a database restored from an old backup could not "replay a year of history" on its
first tick. That framing was wrong on both counts. The applier does not replay: for each
pending `pk` it writes the *current* effective row once, so a burst after a long outage is
one write per affected record, not one per missed change — and that end state is exactly
what the main table is supposed to hold. A skipped row, by contrast, leaves the main table
wrong for as long as nobody notices. The scan is bounded by the number of pending rows
through the partial index (§5.5), not by age. The window survives as an *alert threshold* in
`lex_pending_activations()` (§5.6). There is no settings table.

### 5.4 The floor (#695)

Predicate and writes: **unchanged**. Discovery: **unchanged** — it is Python and may call
`apps.get_models()`. The two paths therefore discover differently, and the parity test in
§13 asserts they find the same triples on the same database.

One asymmetry is accepted for now: #695 bounds the floor by
`LEX_ACTIVATION_RECONCILE_MAX_AGE_DAYS` and skips older rows; the applier applies them
(§5.3). Wherever pg_cron runs, the applier's behaviour wins because it gets there first.
Where only the floor runs (§8.13), old rows are skipped as #695 designed. Revisiting #695's
bound is §12.3, not this document.

It remains the only activator wherever pg_cron is absent, which is why §4.3's rejected third
option — demoting it to detect-only — would have reintroduced the failure this project
exists to remove.

### 5.5 The index

Both paths select on `meta_task_status`, every tick, per meta table, forever. LEX-578
records the floor's scan as "free on small instances, unmeasured on a large one"; this
design doubles it and makes it permanent.

Revision 1 created a partial index from the startup hook. With the hook gone (§3.2) the
index returns to where a table's indexes belong: **declared on the generated meta model** in
`MetaLevelHistoricalRecords`, as an `Index` on `history_object_id` with the condition
`meta_task_status = 'SCHEDULED'`. `SCHEDULED` rows are transient and rare, so the index is
tiny, and it serves the applier's scan, the applier's flip and the floor's scan alike.

The cost LEX-578 flagged is real and accepted: each customer repository picks up one
autogenerated `AddIndex` migration per meta table on its next `makemigrations`, and
lex-app's own test models get theirs in `lex/lex_app/migrations`. **Correctness never
depends on the index**; a project that has not migrated yet pays a sequential scan per meta
table per tick until it does (§9). The alternative — the applier issuing `CREATE INDEX`
itself — would put DDL under the cron job's role and a table lock into a job that fires
every minute; rejected.

### 5.6 `lex_pending_activations()` — visibility

A set-returning function over the same triples: every `SCHEDULED` meta row across every
bitemporal table, with its `main_table`, `pk`, `history_id`, `valid_from`, whether it is
due, and for how long. Read-only; it writes nothing and is not a second truth — it is a
*view* of the first one that happens to span fifty tables.

Three uses. Operators get the single "what is pending" place a queue table would have given
(§4.2) without the dual-truth cost. Monitoring gets its alert: any row due for longer than
the former max-age window means neither activator has run, so the silent-non-activation row
of §9 becomes visible. Tests get their oracle: the row is listed before the tick and not
after.

### 5.7 Three sub-decisions

**No attempt-counter table in v1.** Without a queue table there is no per-activation home
for one, and a `lex_activation_attempts` table would be a queue table under another name —
the dual-truth cost §4.3 rejected. The applier is bounded instead by isolation: a poison row
costs one failed statement per tick, is visible in `cron.job_run_details` and as a row that
stays due in `lex_pending_activations()`, and blocks nothing. #695's in-memory counter
continues to bound the floor. Add the table only if a real poison row is ever observed; §12
lists it.

**Execution role is LEX-136's decision, not this document's.** The function must run as a
role that can write the app's tables. Two workable shapes: register the job with the app
role as `username`, or make the function `SECURITY DEFINER` owned by the app role. The
requirement is stated here; the choice belongs with the permissions work.

**The SQL lives in one file.** `lex/core/sql/bitemporal_activation.sql` holds the three
functions; the migration reads and executes it, with `DROP FUNCTION IF EXISTS` for each as
the reverse. A reviewer reads SQL as SQL rather than as a Python string, and the file is
what the tests load.

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

That acceptance is also §3.2's: the same test, read as a statement about the boundary.
§13's last scenario is its executable form.

---

## 7. What changes, and who notices

| | Before | After |
|---|---|---|
| Non-Celery instances (47) | Timer lost on restart; activation never happens | Applied from the database; survives restarts and scale-to-zero |
| Celery instances (6) | Beat fires a task | Applier fires; beat scheduling retired for activations |
| Dependent calculations | Not recalculated (dead since v2, §2.3) | Unchanged — now explicit and tested |
| UI list views | Not notified of activation | Unchanged (§10.5) |
| Activation cost | One main-row write on the firing thread | Bounded SQL in the database |
| Backend startup | Local scheduler thread; Celery beat rows | Nothing — no registration, no timers (§3.2) |
| New schema | — | Three SQL functions (§5.1); one partial index per meta table (§5.5). **No new tables** |
| Customer repositories | — | One autogenerated `AddIndex` migration per meta table on the next `makemigrations` (§5.5) |
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

**8.11 Very old due rows.** Applied, not skipped. Convergence makes a late apply correct and
a skipped one wrong indefinitely (§5.3). The floor keeps #695's bound for now (§5.4). Rows
due for longer than the window are the alert condition in `lex_pending_activations()` (§5.6).

**8.12 Meta versioning.** The applier mirrors the in-place `UPDATE` (§2.4), touching every
`SCHEDULED` version for the `history_object_id`. It does not open a new meta version. Any
future improvement to that must land on both paths together.

**8.13 No pg_cron.** Local development, CI, SQLite, self-hosted PostgreSQL without the
extension, non-PostgreSQL backends: no applier. The floor is the only mechanism. The
applier's own tests need real PostgreSQL but **not pg_cron**: they call
`lex_apply_due_activations()` directly, which is the right boundary because pg_cron's only
job is to issue that call, and LEX-135 verified on dev that it does. What the tests cannot
cover — the controller's registration and the job's role — is LEX-136's (§8.14).

**8.14 Cross-database registration and role.** LEX-136's subject. Not resolved here; the
requirement on the execution role is in §5.7.

**8.15 Sequential scan.** Both paths select on `meta_task_status` every minute. §5.5.

**8.16 Row locking.** A concurrent user save and an activation can touch one main row.
`sync_record_for_model` takes `select_for_update` on both the history row and the main
row; the applier takes `FOR UPDATE` on the history row and relies on the `INSERT ... ON
CONFLICT` row lock for the main row. Deadlock ordering (history before main) must match
the Python path.

**8.17 Catalog drift.** A model removed from the codebase leaves its tables until a migration
drops them, and the applier keeps applying their pending rows until then — correct, since
the rows exist. A triple with one table gone fails the existence check in §5.2 and is skipped
with a `NOTICE`; nothing raises. There is no registry to fall out of date.

**8.18 Non-`id` primary key.** `sync_record_for_model` uses `model._meta.pk.name`. The
applier reads the main table's primary-key column from `pg_index` (§5.2) and uses it in every
predicate and in the `ON CONFLICT` target. Composite keys are not supported by the existing
code and are skipped with a `NOTICE` here.

**8.19 A customer model defining `get_dependent_entries`.** Nothing in the framework
provides it (§2.3), but a customer repository could. That model would then propagate on
the floor and not on the applier. The parity test in §13 catches it; the fix is to suppress
`post_save` propagation in the activation path unconditionally rather than rely on the
method's absence.

**8.20 Case-sensitive identifiers.** Real project app labels contain capitals, so the
physical table names are quoted identifiers. Every dynamic reference goes through
`format('%I')`, and the existence check in §5.2 does too — an unquoted lookup would report a
present table as missing and skip the model silently. A fixture with a mixed-case name is in
§13.

**8.21 Custom `db_table`.** A bitemporal model that overrides `db_table` breaks rule 2 or 3
of §5.2 and is not discovered. None exists in the framework — the only overrides are the
non-bitemporal `legacy_data` models — nor in the project database checked in §4.3. If one
appears, the fix is a small override table consulted before the rules; added then, not now
(§12.8).

**8.22 A table that matches the shape by accident.** Rule 1 of §5.2 alone could match a
non-lex table with those four columns; rule 4 then also demands sibling tables with lex's
exact names. The combination has no realistic false positive. If one is ever constructed,
the applier attempts a convergence against it and fails in the per-`pk` block with a
`WARNING`.

**8.23 The function does not exist yet.** The controller registers the cron job at
provisioning; lex-app installs the function at its first `migrate`. In between, every tick
logs a failed run in `cron.job_run_details`. Harmless — no `SCHEDULED` row can exist before
lex-app has run either — but noisy. The registered command can guard on
`to_regproc('lex_apply_due_activations')` before calling; that is LEX-136's line to change
(§12.1).

**8.24 Database failover.** pg_cron's job definitions live in a table in the `postgres`
database and survive a Cloud SQL failover. Whether the cron background worker starts on the
new primary is a property of Cloud SQL's pg_cron integration to verify, not assume (§12.1).
Until verified, a failover is a case where the floor may be the only activator for a while.

**8.25 Overlapping ticks.** A slow tick can still be running when the next fires. Safe: the
`FOR UPDATE` on the history row serialises the two per `pk`, and the second finds the meta
row `DONE` or converges to the same state (§8.9). `SKIP LOCKED` on the meta scan would let
the second tick pass over rows the first holds; a refinement if long ticks are ever observed.

---

## 9. Failure modes

The question this table answers is *what if X is not available*, for every X — and what
happens when X comes back.

| Unavailable | While it is down | When it returns | Cost |
|---|---|---|---|
| lex-app, at apply time | The database applies on time; nothing was needed from lex-app | Reads the applied state. The floor finds no due `SCHEDULED` rows; no timer re-fires because none exists | None — this is the design's purpose |
| lex-app, at write time | Nothing is saved, so nothing is scheduled | — | None; there is no separate schedule store to drift |
| pg_cron job never registered, or extension absent | The floor applies within its interval, if lex-app is up | — | Latency |
| pg_cron **and** lex-app | Nothing applies | Whichever returns first converges; the other finds `DONE` rows (§8.9) | Delay; visible in `lex_pending_activations()` |
| The database | Nothing runs and nothing is written | pg_cron resumes and applies the *current* effective state — one write per affected record, not a replay (§5.3) | Delay only |
| The database, by failover | Job definitions survive in `cron.job`; worker restart on the new primary unverified (§8.24) | Applies on the next tick once the worker runs; the floor covers the gap | Unknown until LEX-136 verifies |
| The function (migration not yet run) | Each tick logs a failed run; no `SCHEDULED` row can exist yet either (§8.23) | The first `migrate` installs it | Log noise; guard in LEX-136 |
| Both activators disabled | Nothing | — | Silent non-activation — the state being left. Now visible in §5.6 |
| One row, every tick (poison) | Per-`pk` exception block; `WARNING` in `cron.job_run_details`; the row stays due in §5.6 | — | That record only |
| The index, in a project not yet migrated | Sequential scan per meta table per tick | The project's next `migrate` | Cost only; never correctness (§5.5) |
| One table of a triple | Existence check fails; `NOTICE`; skipped (§8.17) | — | That model only |
| The history row behind a meta row | No effective row → no write | — | None |
| The schema, changed mid-flight | Apply-time column derivation (§8.6) | — | None |

**No double application, in any row above.** Both activators lock the history row and
upsert the main row, and the meta flip is a single `UPDATE` filtered on `SCHEDULED`.
Whichever loses the race writes nothing.

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

- It does not add a per-activation table, and no longer adds a registry table (§4.3).
- It does not run anything in lex-app at apply time — no startup registration, no cache, no
  DDL (§3.2).
- It does not change the meta flip's semantics (§2.4, §8.12).
- It does not change the floor's predicate, writes or discovery (§5.4).
- It does not suppress `post_save` on the activation path, because nothing that runs
  depends on it (§2.3) — but §8.19 records when that would change.

---

## 12. Open items for someone else

1. **LEX-136** — execution role for the function (§5.7); job registration; guard the
   registered command on the function's existence (§8.23); verify the cron worker restarts
   after a Cloud SQL failover (§8.24).
2. **LEX-578** — measure the scan on the largest instance; this design makes the index
   load-bearing for both paths and puts it on the meta model (§5.5), which is the migration
   LEX-578 flagged.
3. **PR #695** — merge is this design's prerequisite; its max-age skip is now an asymmetry
   with the applier (§5.4, §8.11) and should become an alert there too.
4. **Instances with `autoscaling: true`** — the applier serves them and the floor cannot.
5. **Attempt-counter table** — only if a real poison row is observed (§5.7).
6. **Dead dependency propagation, framework-wide** — `DependencyAnalysisMixin` was not
   ported to v2, so `CalculatedModelUpdateHandler.register_save` has returned on its first
   line since the migration, for every save on every model. Not caused or fixed here.
   Needs an issue against the calculation engine, and confirmation against a v1 instance
   that it *did* propagate before this is filed as a regression.
7. **UI notification on activation** — pre-existing gap (§10.5).
8. **Custom `db_table` override** — only if a bitemporal model with one appears (§8.21).
9. **Discovery cost** — cache `lex_bitemporal_tables()` within or across ticks only if the
   catalog queries show up in LEX-578's measurement (§5.2).

---

## 13. How it gets tested

Cluster 05-history, following batch 5n (5.104–5.109, the floor). The applier's tests need
real PostgreSQL and **not** pg_cron (§8.13); the floor's need neither.

**Deliverables under test:**

| File | Holds |
|---|---|
| `lex/core/sql/bitemporal_activation.sql` | The three functions (§5.1) |
| `lex/core/migrations/0001_bitemporal_activation.py` | `RunSQL` installing the file; the reverse drops the functions |
| `lex/core/services/MetaHistory.py` | The partial index declared on the generated meta model (§5.5) |
| `lex/lex_app/migrations/` | `AddIndex` for lex-app's own test models (§5.5) |
| `lex/test_project/tests/history/test_5o_activation_applier.py` | The scenarios below |

**Scenarios:**

- **Producer:** a future-dated save writes a `SCHEDULED` meta row and no timer; rolling
  back leaves nothing.
- **Discovery:** `lex_bitemporal_tables()` returns exactly the fixture's triple with the
  right `pk_column`; a mixed-case table name is found (§8.20); a triple with one table
  dropped is skipped with a `NOTICE` while the others still apply (§8.17); the result does
  not depend on `django_content_type` (§5.2).
- **Visibility:** `lex_pending_activations()` lists the row before the tick and not after;
  a row long past due is reported with its age (§5.6).
- **Supersede / cancel / convergence / deletion:** §8.1–8.4, each against the applier.
- **Late apply:** a row far past its `valid_from` is applied, not skipped (§8.11).
- **Parity — the one that matters most:** applier and floor, run against identical
  fixtures, produce identical `(main, history, meta)` state and discover the same triples.
  This is the test that makes §4.3's argument true in practice.
- **No propagation:** activation through either path leaves other tables untouched.
- **Meta mirror:** the applier's flip touches exactly the rows the Python `.update()`
  touches, and opens no new meta version (§8.12).
- **Timezone:** a `valid_from` either side of a UTC-offset boundary activates at the right
  instant on both paths (§8.7).
- **Idempotence:** applier then floor, and floor then applier, converge (§8.9); the applier
  twice in a row writes nothing the second time (§8.25).
- **Isolation:** one failing `pk` does not prevent the others from applying (§8.10).
- **Boundary:** the apply step is one `SELECT lex_apply_due_activations()` through a raw
  cursor; no lex-app Python runs between the save and the assertion, and all three tables
  reach their final state (§3.2, §6).

---

## 14. Worked example

A fund controller learns in December that a fund's management fee drops on 1 January. She
records it today, so the fee is right on the day without anyone logging in at midnight.

| When | Who | What happens |
|---|---|---|
| 12 Dec, 14:03 | Controller | Opens the fund record, changes the fee from 1.50 to 1.25, sets the effective date to 1 Jan, saves |
| 12 Dec, 14:03 | Backend | Leaves the main row untouched, still 1.50. Writes one history row: a full snapshot with the new fee, `valid_from` 1 Jan 00:00, `history_type` `~`. Writes one meta row, `SCHEDULED`. Commits. Creates no timer and no Celery task (§10.3) |
| 12 Dec, 14:04 | Controller | The grid still shows 1.50, because that is what is true today. The history view shows the pending row and its effective date; `lex_pending_activations()` lists it as not yet due |
| 20 Dec | Platform | The instance is redeployed for a release, then scaled to zero over the holidays. Nothing needs preserving — the schedule is a row |
| 28 Dec | Controller | Changes her mind: 1.30, not 1.25. lex records the correction as it does today; the row effective from 1 Jan now says 1.30. Nothing is rescheduled, because there is nothing to reschedule |
| 1 Jan, 00:00 | Cloud SQL, pg_cron | Fires the standing job. The function lists the bitemporal tables from the catalog (§5.2), finds one `SCHEDULED` meta row whose history row is now effective, locks that row, copies its columns into the main row — 1.30 — marks the meta row `DONE`, commits. One line in `cron.job_run_details` |
| 2 Jan, 09:10 | Controller | Opens the fund. The fee reads 1.30. Nobody touched it, and every calculation from now on reads 1.30 from the main table |

**Where this story breaks today.** On the 20 December redeploy the in-memory timer dies with
the process (§2.1), so on the non-Celery instances the fee never changes. Someone notices in
February when a fee calculation comes out wrong, and the correction is a manual edit with
the wrong audit trail.

**If the backend had been down at midnight.** Same rows, same outcome. The backend was never
part of the midnight step (§3.2).

**If pg_cron had been down at midnight too.** The row sits in `lex_pending_activations()` as
due. The first activator to return — the applier on its next tick, or the floor within a
minute of the backend starting — applies 1.30 once; the other finds `DONE` (§9).

**What the database never knows.** That this is a fee, that 1.30 is a percentage, or what
the fund is. It copies a snapshot when its time arrives (§3.1).
