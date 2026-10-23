## Cluster 5 — History & Bitemporal (existing 5a–5d)

### Batch 5e — Service layer

| Property | Value |
| --- | --- |
| Scenario range | 5.30 – 5.48 |
| Type | I |
| Files covered | `core/services/Bitemporal.py`, `core/services/bitemporal_signals.py`, `core/services/StandardHistory.py`, `core/services/MetaHistory.py`, `process_admin/utils/bitemporal_sync.py` |
| Test file | `lex/test_project/tests/history/test_5e_bitemporal_services.py` |
| Test classes | `TestBitemporalCore` (intervals, valid_from/to chaining), `TestBitemporalSignals` (pre_save → row close + new row), `TestStandardHistory` (non-bitemporal branch), `TestMetaHistory` (cross-model linking), `TestBitemporalSync` (legacy → bitemporal migration) |
| Fixtures | `BitemporalItem`, `LegacyVersionedItem` |
| Est. tests | ~20 |
| Coverage gain | +1.4 % |
| Prereqs | none |

### Batch 5f — History REST endpoint

| Property | Value |
| --- | --- |
| Scenario range | 5.49 – 5.55 |
| Type | E |
| Files covered | `api/views/model_entries/History.py` |
| Test file | `lex/test_project/tests/history/test_5f_history_endpoint.py` |
| Test classes | `TestHistoryEndpointShape`, `TestHistoryFilters` (date range, user), `TestHistoryPermissionGating` |
| Fixtures | reuse 5e fixtures + 4j users |
| Est. tests | ~8 |
| Coverage gain | +0.4 % |
| Prereqs | 5e + 4j |

---

---

### Batch 5m — Edit-time correctness + as_of time-travel round trip ✅ (1 xfail gates BUG-026)

| Property | Value |
| --- | --- |
| Scenario range | 5.98 – 5.103 |
| Type | E |
| Files covered | `api/utils/temporal.py` (`parse_as_of_datetime`), `core/services/Bitemporal.py` (`get_queryset_as_of`), `api/views/model_entries/History.py` (`?as_of` branch), `core/models/LexModel.py` (`lex_datetime_now` / `edited_at`) |
| Test file | `lex/test_project/tests/history/test_5m_asof_edit_time.py` |
| Test classes | `TestCluster05m_AsOfEditTime` |
| Fixtures | reuses `HistSimpleItem` |
| Est. tests | 6 |
| Coverage gain | pins the full timestamp chain end to end (stamp → serialize → parse → compare) |
| Prereqs | BUG-025 fix (Z-serialized datetimes) |
| Status | ✅ Complete — 5 pass / 1 xfail(strict) |
| Note | Customer concern 2026-07-14 ("we rely on the as_of mechanism"). 5.98 edited_at is the true edit instant and its serialized form denotes the same instant; 5.99 as_of before the edit returns exactly the pre-edit snapshot (values included); 5.100 as_of now knows both versions, latest current; 5.101 (xfail strict, **BUG-026**) anchoring as_of on the record's own serialized edited_at must land on the post-edit side — fails today because `edited_at` and `valid_from`/`sys_from` come from separate clock reads (~ms gap). Fix design + the `_history_date` trap are documented in the BUG-026 row. Extension (2026-07-14, customer list-time-travel report): 5.102 the LIST endpoint (`?as_of=`, the grid's surface) shows pre-edit values at a pre-edit UTC anchor and current values at now; 5.103 naive as_of == UTC by contract, and an offset-aware local anchor for the same instant lands identically — pinning the backend as correct while the frontend's naive-local anchors (BUG-F-022) were the real culprit. |

---

### Batch 5n — Reconcile floor (PR #695, in flight — reserved)

| Property | Value |
| --- | --- |
| Scenario range | 5.104 – 5.109 |
| Status | 🚧 In flight on `feat/bitemporal-activation-reconcile` (PR #695); reserved here so batch 5o's range cannot collide with it |

### Batch 5o — In-database activation applier ✅

| Property | Value |
| --- | --- |
| Scenario range | 5.110 – 5.129 |
| Type | I |
| Files covered | `core/sql/bitemporal_activation.sql`, `core/migrations/0001_bitemporal_activation.py`, `core/services/activation_applier.py`, `core/services/MetaHistory.py` (`scheduled_activation_index`), `core/services/bitemporal_signals.py` (`_schedule_future_activation`) |
| Test file | `lex/test_project/tests/history/test_5o_activation_applier.py` |
| Test classes | `TestCluster05o_ActivationApplier` |
| Fixtures | reuses `HistSimpleItem`, `HistAtomicCalc`; three raw-SQL tables with a capitalised prefix (5.113); one raw meta-shaped orphan table (5.114) |
| Est. tests | 20 |
| Coverage gain | the whole apply path is SQL; Python coverage is the write-time gate and the invokers, exercised end to end |
| Prereqs | PostgreSQL (the class auto-skips elsewhere); `core` migration 0001 applied to the test DB (the runner's `migrate` does it) |
| Status | ✅ Complete — 20 pass / 0 fail; history cluster 61 pass / 1 skip / 1 xfail after the change |
| Note | Design: `docs/superpowers/specs/2026-09-16-bitemporal-activation-applier-design.md`. The tests call `lex_apply_due_activations()` directly — pg_cron's only job is to issue that call (verified on dev in LEX-135); "the moment has arrived" is produced by scheduling *in the past* under a patched lex-app clock, since PostgreSQL's `now()` cannot be patched. 5.110/5.111 the producer arms nothing while the applier heartbeat is fresh and behaves exactly as before while it is not; 5.112–5.114 discovery from the catalog alone (content types deleted; a capitalised, quoted triple; an incomplete triple skipped with a NOTICE while the others apply); 5.115/5.116 the pending view before/after a tick and the age of a row due for 400 days; 5.117 the happy path plus the heartbeat row; 5.118 supersede; 5.119 CANCELLED and orphan rows untouched; 5.120 three due rows converge in one write; 5.121 deletion; 5.122 a row due for a year is applied, not skipped; 5.123 parity with `activate_history_version` on every shared column; 5.124 nothing but the three tables changes; 5.125 the meta flip touches every SCHEDULED version and nothing else; 5.126 due is decided by the instant, not the offset; 5.127 a second tick and the Python task change nothing; 5.128 a poison record fails alone, counted in the heartbeat and named in a WARNING; 5.129 the boundary — one `SELECT` between save and final state with every Python entry point instrumented to fail. Two side findings fixed in the same change: the legacy local-timer path reused its unique `meta_task_name` when a future row was re-chained within one second (now uuid-suffixed like the Celery branch), and `E2ETestCase` now clears the applier heartbeat around every test — it is not a Django model, so the flush never removed it and a kept test DB carried it into batch 5l. |
