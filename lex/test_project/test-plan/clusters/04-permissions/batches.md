## Cluster 4 — Permissions (existing 4a–4i)

### Batch 4j — Middleware & bearer-token authentication

| Property | Value |
| --- | --- |
| Scenario range | 4.35 – 4.46 |
| Type | I |
| Files covered | `api/middleware/keycloak_permissions.py`, `authentication/authentication_backends/BearerMiddlewareAuthentication.py` |
| Test file | `lex/test_project/tests/permissions/test_4j_keycloak_middleware.py` |
| Test classes | `TestKeycloakPermissionMiddleware` (request → UserContext attachment, scope evaluation, denial path), `TestBearerMiddlewareAuthentication` (valid token, expired, missing, malformed) |
| Fixtures | mock Keycloak token decoder |
| Est. tests | ~12 |
| Coverage gain | +0.7 % |
| Prereqs | none |

### Batch 4k — Permission views

| Property | Value |
| --- | --- |
| Scenario range | 4.47 – 4.55 |
| Type | E |
| Files covered | `views/permissions/ModelPermissions.py`, `views/permissions/UserPermission.py` |
| Test file | `lex/test_project/tests/permissions/test_4k_permission_views.py` |
| Test classes | `TestModelPermissionsEndpoint`, `TestUserPermissionEndpoint` |
| Fixtures | superuser, regular user, group-membership fixture |
| Est. tests | ~9 |
| Coverage gain | +0.4 % |
| Prereqs | 4j |

### Batch 4l — User API endpoint *(blocked — see §6 decision #2)*

`UserAPIView.py` vs `user_api.py` — slot once supervisor confirms which is live.

---

### Batch 4m — `ApiKeyAwareLoginRequiredMiddleware` instance-key bypass (Session 80 — June 18)

| Property | Value |
| --- | --- |
| Scenario range | 4.66 – 4.70 |
| Type | U |
| Files covered | `lex/authentication/middleware.py` (`ApiKeyAwareLoginRequiredMiddleware.check_login_required`) |
| Test file | `lex/test_project/tests/permissions/test_4m_api_key_middleware.py` |
| Test classes | `TestCluster04m_ApiKeyAwareMiddleware` (4.66–4.70 — instance key bypass, DRF key bypass, non-key delegates to parent, instance check still evaluated when DRF check false, subclass contract) |
| Fixtures | none — `SimpleTestCase` with `patch` on `is_instance_api_key_request`, `is_api_key_request`, and parent `check_login_required` |
| Tests landed | 5 pass / 0 fail |
| Coverage gain | `lex/authentication/middleware.py` new `is_instance_api_key_request` branch |
| Status | ✅ Complete (Session 80 — June 18) |

---

### Batch 4x — per-user Keycloak calls stay with their user; forged tokens and look-alike paths ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.138 – 4.147 |
| Type | U (4.138–4.143), I (4.144–4.147) |
| Files covered | `lex/api/keycloak/uma_client.py` (new), `lex/api/views/authentication/KeycloakManager.py` (`get_uma_permissions`, `get_userinfo`, `get_user_permissions`), `lex/api/middleware/keycloak_permissions.py`, `lex/authentication/views/token_views.py`, `lex/streamlit_app.py` (`get_user_info`), `lex/lex_app/settings.py` (`OIDC_RP_SIGN_ALGOS_ALLOWED`, `OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS`) |
| Test file | `lex/test_project/tests/permissions/test_4x_keycloak_call_isolation.py` |
| Test classes | `TestCluster04x_ConcurrentUsersStayApart` (4.138–4.139), `TestCluster04x_KeycloakAnswersAreClassified` (4.140–4.143), `TestCluster04x_ForgedBearerTokens` (4.144), `TestCluster04x_AnchoredLoginExemptions` (4.145–4.147) |
| Fixtures | a threaded stub Keycloak (`_StubKeycloak`) answering UMA and userinfo for whichever bearer token a request carries; an RSA key pair per module; explicit OP settings so the bearer backend starts without network; a 1 µs thread switch interval in 4.138–4.139, so the interleavings of a loaded server happen in the test too (at CPython's default 5 ms the race stays hidden) |
| Tests landed | **10 pass / 0 fail** against django-oauth2-codeflow 2.1.0 — Mutation, with KeycloakManager.py, keycloak_permissions.py and settings.py put back to HEAD: 4.138 and 4.139 fail with other users' permissions and profiles; 4.140 (None, not []), 4.142 (an unreachable Keycloak goes unlogged), 4.144 (no RS256-only setting), 4.146 (6 look-alike paths skip sign-in) and 4.147 (14 unanchored patterns) fail too; 4.141, 4.143 and 4.145 pass. |
| Also changed | `test_4g_user_permissions_api.py` 4.32/4.32b: their fake manager answers `get_userinfo`, because the middleware no longer reaches into the shared python-keycloak connection (`km.oidc`). Intent unchanged. |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-auth-hotfixes.md) |

**Allocation note:** Letter x, not s-w: those are in flight on feat/multi-value-filters (s, t) and the feat/ext-* branches (s-w, scenarios up to 4.137). Same letter and IDs on lex-app-v2 and release/2.1.x.

---
