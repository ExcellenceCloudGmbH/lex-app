## 9. Signals & WebSocket

**What it tests:** `ActiveCalculationStateStore` tracking, `WebSocketNotifier` broadcasts, `CacheManager` cleanup, and `update_calculation_status` signal.

**Why ninth:** Real-time UI updates are the last user-facing layer. If signals are broken, the UI shows stale data but the backend still works.

**Models needed:**
- `AtomicCalc` (reused)
- `ParentCalc`, `ChildCalc` (reused)

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.1 | `mark_in_progress` registers record in state store | Record retrievable by `get_calculation_id` |
| 9.2 | Calculation completion cleans up state store | Record removed after SUCCESS/ERROR |
| 9.3 | WebSocket notification sent on state change | `send_calculation_update` called with correct model/state |
| 9.4 | Root process cleans up cache | `CacheManager.cleanup_calculation` called for root |
| 9.5 | Child process skips cache cleanup | Cleanup NOT called for child process |
| 9.6 | `update_calculation_status` called with error details on failure | Exception details and stack trace included |
| 9.43 | `from lex.core.signals import *` succeeds | Binds `update_calculation_status`, `do_post_save` and `custom_post_save` — `CalculationSignals`' own functions |

### 9j. A WebSocket serves only a live sign-in (HTTP auth contract v2 close codes) ✅

**What it tests:** the five authenticated sockets (`ws/logs`, `ws/calculations`, `ws/calculation_logs/<id>`, `ws/calculation_status_update`, `ws/model_data_update`) through `lex.lex_app.asgi.application`: under `LEX_WS_AUTH=enforce` a socket without a live sign-in is accepted, then closed with 4401 (anonymous, sign-in ended — at connect, also when it ends while the socket joins its group, on the library's group message, at the 5-minute re-validation, which also sees an expired refresh token, a Django session that is gone and a deactivated user, and reaches synchronous consumers through the channel layer), 4403 (the consumer's `lex_socket_authorize` refuses) or 4503 (the sign-in cannot be checked, e.g. a dropped database connection); `log` serves everything as before, never broken by a failing check, and warns once per socket; `off` is the old behaviour; `ws/health` stays public; socket code never refreshes tokens; `api/auth/session` advertises `ws_close_codes` only under `enforce`.

**Why a regression matters:** anonymous sockets receive live customer data (calculation states, record mutations, logs), or clients cannot tell "sign in again" from a network blip and reconnect forever.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 9.53 | anonymous sockets, every route (`enforce`) | accept, then close 4401; no broadcast and no calculation snapshot reaches them; the five consumers carry the mixin |
| 9.54 | signed-in sockets, every route | served, broadcast delivered (status socket gets its snapshot), closed 4401 by the sign-in's `oidc.session.ended` message |
| 9.55 | back-channel logout | the provider's POST to `/oidc/backchannel-logout` closes Ada's socket 4401; Bob's stays open |
| 9.56 | re-validation | default 300 s; a row ended without a message closes the socket 4401 at the next round |
| 9.57 | never refreshes | an expired access token with a live sign-in is served across re-validations; zero token-endpoint calls |
| 9.58 | who is signed in | a Django login of another backend is served; an OIDC login whose sign-in ended is closed 4401 |
| 9.59 | `ws/health` public | anonymous health socket answers `{"status": "Healthy :)"}` under `enforce` |
| 9.60 | forbidden | `lex_socket_authorize` refusing → accept + 4403; allowing → served |
| 9.61 | auth unavailable | sign-in rows unreadable (database error) → 4503; the hook raising `KeycloakUnavailable` → 4503 |
| 9.62 | `log`, anonymous | served exactly as before (broadcasts delivered, not closed); one WARNING naming consumer, path, 4401, reason |
| 9.63 | `log`, sign-in ended | stays open and served; one WARNING (4401, `session_ended`) for two announcements |
| 9.64 | `off` | anonymous and signed-in sockets served; the end of a sign-in does not close; no WARNING |
| 9.65 | capability | `api/auth/session` → `ws_close_codes` true under `enforce`, false under `log` and `off` |
| 9.66 | the flag | unset/empty → `log`; case-insensitive; `enforced` → `ImproperlyConfigured` listing `off, log, enforce` |
| 9.67 | synchronous consumers | `LexSyncSocketAuthMixin`: anonymous → 4401; signed-in served, closed 4401 when the sign-in ends |
| 9.68 | dropped database connection (review) | `InterfaceError` reading sign-in rows: `enforce` → accept + 4503; `log` → served as before, the would-be 4503 logged |
| 9.69 | ends while joining (review) | the sign-in ends between the ASGI middleware's read and `group_add`: `enforce` → 4401 at connect; `log` → served, `session_ended` warned |
| 9.70 | Django session gone (review) | session deleted without the library (Keycloak sign-in and a Django login of another backend): 4401 at the next re-validation |
| 9.71 | deactivated user (review) | `is_active = False` → 4401 at the next re-validation |
| 9.72 | refresh token expired (review) | `refresh_expires_at` passed, nobody refreshed → 4401 at the next re-validation; a sign-in without a refresh token whose access token expired → 4401 |
| 9.73 | synchronous re-validation (review) | a project's sync consumer: the ticker's `lex.socket.revalidate` message → check → 4401 after the session is deleted |

**Scenario range:** 9.53 – 9.73 (letters h and i, 9.44 – 9.52, are held by the in-flight branch `integration/lex-3-extensions`; 9.68 – 9.73 from the security review of 2026-11-06). **Test file:** `lex/test_project/tests/signals_ws/test_9j_socket_sign_in.py`. **Type:** I. **Status:** ✅ Complete (2026-11-06; review fixes the same day). Batch: [9j](batches.md).

---
