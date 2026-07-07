## Cluster 9 — Signals & WebSocket (existing 9a)

### Batch 9b — Consumers (excluding usage-blocked ones)

| Property | Value |
| --- | --- |
| Scenario range | 9.10 – 9.24 |
| Type | I |
| Files covered | `consumers/CalculationsConsumer.py`, `consumers/UpdateCalculationStatusConsumer.py`, `consumers/LogConsumer.py`, `consumers/BackendHealthConsumer.py` |
| Test file | `lex/test_project/tests/websocket/test_consumers.py` |
| Test classes | one per consumer (4 classes). Use Channels' `WebsocketCommunicator`. |
| Fixtures | in-memory channel layer (`CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}`) |
| Est. tests | ~14 |
| Coverage gain | +0.7 % |
| Prereqs | none |
| Note | Supervisor's list had a typo — `ex/api/consumers/CalculationsConsumer.py` → corrected to `lex/…`. |

`CalculationLogConsumer.py` is **parked** until §6 decision #3 confirms it's still wired anywhere.

### Batch 9e — Generic CRUD mutation broadcast (live list refresh — June 3)

| Property | Value |
| --- | --- |
| Scenario range | 9.29 – 9.36 |
| Type | U + I + E |
| Files covered | `core/signals/ModelMutationSignal.py` (new), `api/consumers/ModelDataUpdateConsumer.py` (new), `lex_app/routing.py`, `api/views/model_entries/One.py`, `api/views/model_entries/Many.py` |
| Test file | `lex/test_project/tests/signals_ws/test_9e_model_mutation_broadcast.py` |
| Test classes | `TestCluster09e_ModelMutationBroadcastHelper` (I), `TestCluster09e_ModelDataUpdateConsumer` (U), `TestCluster09e_CrudTriggersBroadcast` (E) |
| Fixtures | `SimpleItem` / `ALL_MODELS` from `crud_api/models.py`; `E2ETestCase` (TransactionTestCase so commits fire `on_commit`) |
| Est. tests | 8 |
| Coverage gain | new files (broadcast helper + consumer) covered end-to-end |
| Prereqs | none |
| Status | ✅ Complete — 8 pass / 0 fail locally (Postgres test DB available) |
| Note | Fixes the customer-visible "open list view goes stale until manual Refresh" bug: plain CRUD on a non-`CalculationModel` now emits a `model_data_update` `record_mutation` over WebSocket. Generic broadcast is skipped on `calculate=true` updates (`calculation_success` already refreshes). Frontend `ModelDataUpdate` listener lands in the same change. |

### Batch 9f — Core health/calculation/log WebSocket consumers (coverage task #620) ✅

| Property | Value |
| --- | --- |
| Scenario range | 9.37 – 9.42 |
| Type | U |
| Files covered | `lex/api/consumers/BackendHealthConsumer.py`, `lex/api/consumers/CalculationsConsumer.py`, `lex/api/consumers/CalculationLogConsumer.py` |
| Test file | `lex/test_project/tests/signals_ws/test_9f_core_consumers.py` |
| Test classes | `TestCluster09f_BackendHealthConsumer` (9.37 connect/receive health payload, 9.38 disconnect untracks), `TestCluster09f_CalculationsConsumer` (9.39 joins calculations group and forwards ID/notification events, 9.40 disconnect leaves group), `TestCluster09f_CalculationLogConsumer` (9.41 per-record log group and log envelope), `TestCluster09f_ShutdownDisconnectAll` (9.42 shutdown calls `disconnect(None)` on active consumers for all three classes) |
| Fixtures | none — consumer instances with mocked channel layer / socket boundary |
| Tests landed | **6 pass / 0 fail** (direct pytest) |
| Coverage gain | Core consumer connect/disconnect/send branches + `disconnect_all` classmethods for the three coverage-task files |
| Prereqs | none |
| Status | ✅ Complete (Session 81 — June 18). `CalculationLogConsumer.py` is no longer parked: PR #615 wires it in `authenticated_websocket_urlpatterns()`. |

---
