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

### Batch 4n — `PermissionResult` value-object contract ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.75 – 4.82 |
| Type | U |
| Files covered | `lex/core/models/LexModel.py` (`PermissionResult` dataclass) |
| Test file | `lex/test_project/tests/permissions/test_4n_permission_result.py` |
| Test classes | `TestCluster04n_PermissionResult` (4.75–4.82 — allow_all factory sets allowed+null-fields, get_fields returns all; allow_fields limits to specified set + accepts list; allow_all_except excludes listed fields; deny sets allowed=False + empty get_fields; deny_all is alias; __str__ reflects state) |
| Fixtures | none — pure Python, `SimpleTestCase`, no DB |
| Tests landed | **8 pass / 0 fail** |
| Coverage gain | `lex/core/models/LexModel.py` `PermissionResult` factory methods + `get_fields` resolver |
| Status | ✅ Complete — Coverage-task #673 for PR #672 |

---
