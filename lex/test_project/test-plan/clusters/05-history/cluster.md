## 5. History & Bitemporal

**What it tests:** That every `.save()` on a `LexModel` creates a history row, that `valid_from`/`valid_to` chain correctly, and that the MetaHistory layer works.

**Why fifth:** Customers start looking at history after they've been editing records for a while. "What changed and when?" is a compliance requirement.

**Documented contract** (from `docs/features/tracking/history.md` + `docs/features/tracking/bitemporal history.md` + `docs/interface/record-detail/history tab.md` + `docs/features/tracking/tracking tables.md`): every `LexModel` save / update / delete produces a Level-1 `Historical*` row with a **full snapshot of all field values** (not just the changed ones) + `history_type` (`+`/`~`/`-`) + `history_user` (the person who edited the record, or the user who launched the calculation that produced this change) + optional `history_change_reason` (currently only writable from code, not from the UI). Unlike audit logging (which is API-only), history is triggered at the ORM level by every `save()` — both API-driven edits and programmatic/calculation-driven writes produce history rows. Rows are auto-chained so each row's `valid_to` matches the next row's `valid_from` and the latest row carries `valid_to=NULL`; a Level-2 `MetaHistorical*` row records *system time* (`sys_from`/`sys_to`) for every Level-1 change so retroactive `valid_from` corrections are themselves auditable; opt-outs (`untrack()` / `track()` / `save_without_historical_record()` / `bulk_create(skip_history=True)` / `untracked_models` in `model_structure.yaml` / `suspend_bitemporal()` for derived calc outputs) keep the customer in control of overhead; time-travel via `get_queryset_as_of(Model, t)` (valid time) or `get_queryset_as_of(HistoryModel, t)` (system time), and via `GET /api/<model>/<id>/history/?as_of=...` from the UI's *As-Of* control.

**Models needed:**
- `SimpleItem` (reused from Cluster 2)
- `AtomicCalc`, `NonAtomicCalc` (from Cluster 7)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 5.1 | Create a record | Exactly 1 history row, type = "+" (created) |
| 5.2 | Update a record | New history row, type = "~" (changed), `valid_from` = `edited_at` |
| 5.3 | Delete a record | New history row, type = "-" (deleted) |
| 5.4 | Multiple updates | History rows ordered by `history_date`, `valid_to` of row N = `valid_from` of row N+1 |
| 5.5 | `skip_history_when_saving` | No history row created for that save |
| 5.6 | Calculation success history | History shows NOT_CALCULATED → IN_PROGRESS → SUCCESS (3 rows) |
| 5.7 | Calculation failure history | History shows NOT_CALCULATED → IN_PROGRESS → ERROR (3 rows) |
| 5.8 | History survives failed atomic calculation | IN_PROGRESS history row is NOT rolled back (**known bug — expected failure**) |
| 5.9 | History via API (`/history/` endpoint) | Returns correct history rows with field values at each point in time |
| 5.10 | Concurrent edits produce distinct history rows | Two rapid saves → two distinct history entries |

> **Audit notes — May 5.** Walked the implementation against `docs/features/tracking/`:
> * **5.4 partial.** `test_5_4` only asserts ascending `history_id`; the docstring's "valid_to of row N = valid_from of row N+1" half is *not* yet pinned. New gap sub-cluster **5g** (below) closes it as scenario **5.61**.
> * **5.5 narrow.** `skip_history_when_saving` is one of *four* documented suppression toggles (`skip_history_when_saving`, `save_without_historical_record()`, `untrack()`/`track()`, `bulk_create(skip_history=True)`). The other three are uncovered — see **5h** scenarios 5.62–5.65.
> * **5.9 thin.** Only HTTP 200 + ≥3 rows asserted. The documented response shape (`history_id` / `valid_from` / `valid_to` / `history_type` / `user` / `snapshot` / `system_history`) and the `?as_of=...` system-time time-travel branch are *not* asserted — see **5i** scenarios 5.71–5.74.
> * **History snapshot contract not asserted anywhere.** Docs guarantee each history row carries a full snapshot of every field at that moment ("if your model has 10 fields, every history row has all 10"). 5.1/5.2/5.3 only check counts and types. See **5j** scenario 5.75.
> * **`history_user` actor not asserted.** API-driven save must stamp `history_user` to the authenticated user; not pinned anywhere. See **5j** scenario 5.76.
> * **MetaHistory (Level 2) positive contract uncovered.** 9.7–9.10 cover the suppression *primitives* but no test ever asserts that a save *creates* a MetaHistorical row, that `sys_from`/`sys_to` chain, or that an `history_object` FK points back to L1. See **5k** scenarios 5.81–5.84.
> * **`suspend_bitemporal()` positive contract uncovered.** 9.7–9.10 lock down the underlying ContextVars; the customer-facing CM (`with suspend_bitemporal(): obj.save()` → 1 query, 0 history rows, 0 meta rows) is not exercised. See **5h** scenario 5.66.
> * **`untracked_models` config not tested.** Documented `model_structure.yaml` opt-out at the *model* level (no `Historical*` table generated) — see **5h** scenario 5.67 (deferred-fixture).
> * **Time-travel helpers `get_queryset_as_of(...)` uncovered.** Both branches (main-model → valid time; history-model → system time) — see **5i** scenarios 5.72–5.73.

---

### 5m. Edit-time correctness + as_of time-travel round trip ✅ (BUG-026 gate)

**What it tests:** the full timestamp chain a client depends on when time-traveling: the edit stamps `edited_at` (naive UTC), the API serializes it with an explicit `Z` (BUG-025 fix), `parse_as_of_datetime` normalizes the query value back to naive UTC, and `get_queryset_as_of` compares it against the `valid_from`/`sys_from` windows. A shift anywhere returns plausible wrong data instead of an error.

**Why a regression matters:** `as_of` answers "what did this record look like before the edit?" — audit-grade functionality customers explicitly rely on.

**Scenario range:** 5.98 – 5.103. **Test file:** `lex/test_project/tests/history/test_5m_asof_edit_time.py`. **Type:** E. **Status:** ✅ Complete — 5 pass; 5.101 `xfail(strict)` pins **BUG-026** (`edited_at` vs history-window clock-read gap; anchoring `as_of` at a record's own `edited_at` misses the edit).
