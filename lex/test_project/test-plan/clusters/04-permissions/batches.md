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

### Batch 4n — Keycloak UMA permissions in Reflex dashboards ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.75 – 4.79 |
| Type | U |
| Files covered | `lex/lex_app/reflex/auth.py` (`LexKeycloakAuthState._lex_uma_permissions`, `._reset_auth`, `current_permissions`, `current_access_token`, `has_permission`, `_fetch_uma_permissions`, `_token_subject`) |
| Test file | `lex/test_project/tests/permissions/test_4n_reflex_permissions.py` |
| Test classes | `TestCluster04n_PermissionLookup` (4.75–4.77), `TestCluster04n_HasPermission` (4.78), `TestCluster04n_AccessToken` (4.79) |
| Fixtures | a session's Reflex state tree (root `State`); access tokens put in the provider's cookie as sign-in and refresh do; `KeycloakManager` patched as the one external boundary; `KeycloakItem` as the model a check names |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.75 | one lookup per access token | `[]` signed out without asking Keycloak; exactly `KeycloakManager`'s answer signed in, asked once however often it is read and again after a refresh; the session keeps a digest of the token, never the token; callers get a copy |
| 4.76 | failures degrade safely | a lookup that raises, answers nothing or answers an error body keeps the same user's grants and is retried on the next read; a different user signing in while Keycloak fails gets `[]`, never the previous user's grants |
| 4.77 | sign-out forgets them | `_reset_auth` clears the cached grants with the tokens; the next user gets a lookup of their own |
| 4.78 | `has_permission(target, scope)` | a model resolves to `<app_label>.<ModelName>`; only a model-wide grant of the scope counts (`read` by default) — not a record grant, not another resource's, not a case variant; signed out or unanswered is a denial; anything but a model or a name is refused where the check is declared |
| 4.79 | any state reaches the token | a dashboard's own state gets the session's access token (`""` signed out) and permissions |

Every rule above was checked as a guard, not decoration: five mutations of `auth.py` — keep grants
across users, keep them across sign-out, count record grants, cache the raw token, return the cached
list itself — each fail exactly one of these scenarios. The rule for record grants is the API's own
(`lex/api/utils/helpers.py`, `LexModel`): a `resource_set_id` scopes a grant to one record, so it
must not open a check on the whole model.

---
