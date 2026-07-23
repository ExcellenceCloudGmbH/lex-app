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
