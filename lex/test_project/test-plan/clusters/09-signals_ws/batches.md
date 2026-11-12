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

### Batch 9g — `lex.core.signals` star import: `__all__` names only what the package provides ✅

| Property | Value |
| --- | --- |
| Scenario range | 9.43 |
| Type | U |
| Files covered | `lex/core/signals/__init__.py` |
| Test file | `lex/test_project/tests/signals_ws/test_9g_signals_package_exports.py` |
| Test classes | `TestCluster09g_SignalsPackageExports` (9.43 the star import works and binds the three calculation-signal functions, `CalculationSignals`' own) |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail** (3 subtests) |
| Coverage gain | the package's star-import contract |
| Prereqs | none |
| Status | ✅ Complete |
| Note | `create_profile` and `save_profile`, `User` receivers that created a `Profile` the module never imported, were deleted from `CalculationSignals.py` in February, and their import into the package a day later. Both names stayed in `__all__`. A star import binds every name there, so `from lex.core.signals import *` raised `AttributeError` and lost the calculation-signal API with it. Both names are dropped. |

---

### Batch 9j — A WebSocket serves only a live sign-in (auth contract v2 close codes, `LEX_WS_AUTH`) ✅

| Property | Value |
| --- | --- |
| Scenario range | 9.53 – 9.73 (9.68 – 9.73 from the security review of 2026-11-06) |
| Type | I |
| Files covered | `lex/api/consumers/auth.py` (new: `LexSocketAuthMixin`, `LexSyncSocketAuthMixin`, `resolve_socket_sign_in`, `sign_in_active`, `ws_auth_mode`; since the review `sign_in_alive`, the post-join re-check, the whole check guarded, the synchronous re-validation ticker `REVALIDATE_MESSAGE`), `lex/api/consumers/CalculationsConsumer.py`, `CalculationLogConsumer.py`, `LogConsumer.py`, `UpdateCalculationStatusConsumer.py`, `ModelDataUpdateConsumer.py`, `lex/lex_app/asgi.py` (`OIDCSessionASGIMiddleware` around the authenticated routes), `lex/lex_app/routing.py` (docstring), `lex/lex_app/settings.py` (`LEX_WS_AUTH`), `lex/authentication/capabilities.py` (`ws_close_codes`) |
| Test file | `lex/test_project/tests/signals_ws/test_9j_socket_sign_in.py` |
| Test classes | `TestCluster09j_EnforcedSockets` (9.53–9.59), `TestCluster09j_ForbiddenAndUnavailable` (9.60–9.61), `TestCluster09j_RolloutModes` (9.62–9.66), `TestCluster09j_SyncConsumers` (9.67), `TestCluster09j_ReviewConnect` (9.68–9.69), `TestCluster09j_ReviewRevalidation` (9.70–9.73) |
| Fixtures | `TransactionTestCase` (Channels closes old connections around each database call); `asgiref.testing.ApplicationCommunicator` on `lex.lex_app.asgi.application` (`channels.testing` needs daphne) with same-origin headers and the session cookie; sign-ins from `lex.lex_app.tests.auth.sign_in`; the in-memory channel layer (flushed per test); django-oauth2-codeflow's `FakeOP` for the back-channel logout and the token-endpoint count; `connection.execute_wrapper` failing `OIDCSession` queries for 4503; `LEX_WS_AUTH` per test through `addCleanup` (a method-level `@override_settings` is undone before the sign-in helper's cleanup and leaked the mode into later tests); fresh interpreters for the flag; since the review: `connection.execute_wrapper` raising `InterfaceError` (9.68), an ASGI wrapper that ends the sign-in between the middleware and the consumer (9.69), `oidc_revalidate_seconds` patched to 0.2 s and a project's synchronous consumer (9.70–9.73) |
| Tests landed | **21 pass / 0 fail** (25 subtests); before the review fixes 15/0 (17 subtests); the existing 9e/9f consumer tests unchanged and passing under the default `log` |
| Mutation check | With `lex_socket_connect` serving every socket (the 2.4 behaviour), 17 cases fail (9.53–9.56, 9.58, 9.60–9.63; all subtests of 9.53/9.54). Review fixes, each undone in a scratch copy of the source: catching only `DatabaseError` and not guarding the check → 9.68 (both modes); no re-check after `group_add` → 9.69 (both modes); no Django-session check → 9.70 (both sockets); no `is_active` check → 9.71; no `refresh_expires_at` check → 9.72; no check of a row without a refresh token → 9.72 (that subtest); no synchronous ticker → 9.73 |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-surfaces-reflex-ws.md) |

**Allocation note:** Letter j, not h: h (9.44 – 9.48) and i (9.49 – 9.52) are held by the in-flight branch `integration/lex-3-extensions` (and the ext-5…ext-11 branches under it), which also changes `ModelDataUpdateConsumer.py`; expect a small textual conflict there when both land.

**Why a regression matters:** live customer data reaches anonymous sockets (BUG-047), or a client cannot tell "sign in again" (4401) from "stop" (4403) or "retry later" (4503) and reconnects forever.

---
