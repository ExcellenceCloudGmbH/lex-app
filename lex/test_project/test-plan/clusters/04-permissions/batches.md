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

### Batch 4o — Generic API gate and OIDC redirect guard ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.80-4.85 |
| Type | U + E |
| Files covered | `lex/api/middleware/generic_api_gate.py`, `lex/authentication/middleware.py` (OidcRedirectGuardMiddleware), MIDDLEWARE in `lex/lex_app/settings.py` |
| Test file | `lex/test_project/tests/permissions/test_4o_generic_api_gate_and_redirects.py` |
| Test classes | `TestCluster04o_GenericApiGate`, `TestCluster04o_OidcRedirectGuard`, `TestCluster04o_GateAndGuardWiring` |
| Fixtures | RequestFactory requests with stand-in users; the E2E client for the wiring scenarios |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |
| Note | The guard accepts the bundled frontend's absolute next URLs (window.location.href) because the frontend is served from the site's own origin or a configured dev origin. |

### Batch 4p — Secure permission defaults on the generic REST API ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.86-4.103 |
| Type | E |
| Files covered | `lex/api/security.py`, `lex/api/views/model_entries/{One,Many,History,filter_backends}.py`, `lex/api/views/process_flow/CreateOrUpdate.py`, `lex/api/views/global_search_for_models/Search.py`, `lex/api/views/file_operations/{ModelExport,FileDownload}.py`, `lex/api/filters/GenericFilters.py`, `lex/process_admin/views/model_relation_views.py` (Overview), `lex/core/models/{LexModel,HTMLReport}.py`, `lex/api/serializers/base_serializers.py` |
| Test file | `lex/test_project/tests/permissions/test_4p_secure_defaults.py` |
| Test classes | `TestCluster04p_FailClosedAndNotFound`, `TestCluster04p_EditEnforcement`, `TestCluster04p_ReadSurfaces`, `TestCluster04p_ReadRulesAtScale` |
| Fixtures | `RaisingReadItem`, `QuerysetRuleItem`, `SecretDocument` (FileField) plus the existing ProtectedItem/KeycloakItem/FilterBackendItem; a superuser-only HTMLReport registered for one test |
| Tests landed | **18 pass / 0 fail** |
| Status | ✅ Complete |
| Note | No new denial answers 403: the bundled frontend signs the user out on 403, so unreadable records are 404 and refused writes 400. Single-record visibility uses permission_read for the record (the decision the serializer made before), so classic API keys keep reading what they could read. |

### Batch 4q — Scoped API keys (LexAPIKey) ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.104-4.110 |
| Type | E |
| Files covered | `lex/authentication/models/_api_keys.py`, migration 0002_lexapikey, `lex/api/utils/api_key_requests.py`, `lex/authentication/middleware.py`, `lex/authentication/authentication_backends/api_keys.py`, `lex/api/middleware/keycloak_permissions.py`, `lex/core/models/LexModel.py` ('*' scope grant), `filter_backends.py` |
| Test file | `lex/test_project/tests/permissions/test_4q_scoped_api_keys.py` |
| Test classes | `TestCluster04q_ScopedApiKeys` |
| Fixtures | a service user; `KeycloakItem` (default permissions); keys created with LexAPIKey.objects.create_key; a resolver function in the test module |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |
| Note | LexAPIKey lives in an underscore module so model discovery does not register it with the generic API (key hashes are never listed). |

### Batch 4r — Audit authors for users without names ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.111-4.113 |
| Type | E |
| Files covered | `lex/audit_logging/mixins/AuditLogMixin.py` (log_change author), `lex/audit_logging/mixins/BulkAuditLogMixin.py` (log_change author, _audit_author) |
| Test file | `lex/test_project/tests/permissions/test_4r_audit_authors.py` |
| Test classes | `TestCluster04r_AuditAuthors` |
| Fixtures | `KeycloakItem`; framework tables `AuditLog`, `AuditLogStatus`; scoped `LexAPIKey`s |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Same fallback as the model's created_by (LexModel._normalize_audit_actor). |
