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
