## 4. Permissions

**What it tests:** Field-level (`permission_read/edit/export` → `PermissionResult`) and action-level (`permission_create/delete/list` → `bool`) access control. This is how customers protect sensitive data.

**Why fourth:** After basic CRUD and validation, the customer asks "who can see what?" Permissions control the answer.

**Models needed:**
- `ProtectedItem` — `LexModel` with custom `permission_read/edit/delete` overrides
- `FieldLevelItem` — `LexModel` with `allow_fields()` / `allow_all_except()` patterns
- `KeycloakItem` — `LexModel` using default Keycloak scope-based permissions

**Test scenarios:**

| # | Scenario | What We Assert |
|---|----------|----------------|
| 4.1 | Superuser reads all fields | `permission_read` returns `allow_all` |
| 4.2 | Regular user reads allowed fields only | API response only includes permitted field names |
| 4.3 | `allow_all_except` hides sensitive fields | Excluded fields absent from API response |
| 4.4 | `permission_edit` restricts editable fields | PATCH to restricted field is rejected or ignored |
| 4.5 | `permission_delete` denies deletion | DELETE returns 403 |
| 4.6 | `permission_create` denies creation | POST returns 403 |
| 4.7 | Keycloak scope fallback — read scope present | Default `permission_read` allows all fields |
| 4.8 | Keycloak scope fallback — no scopes | Default `permission_read` denies |
| 4.9 | Legacy `can_read()` compatibility | `can_read(request)` returns same fields as `permission_read` |
| 4.10 | `UserContext.from_request` builds correct context | Groups, scopes, roles, email correctly populated |
| 4.11 | API key context | `client_roles` includes "api_key", scopes from key identity |
| 4.12 | `with_instance` resolves instance-specific Keycloak scopes | Scopes matched by `rsname` and `resource_set_id` |
| 4.40 | `permission_export` full deny at the export endpoint (sub-cluster 4h) | POST `/api/<model>/export` → 200 with rows present (read open) but every domain field blanked; only the framework's `{id, created_by, edited_by}` columns may carry data. Pins the union behaviour in `ModelExportView.get_exportable_fields_for_object` against both over-restrictive (rows dropped) and over-permissive (domain leaks) drift. |
| 4.41 | Full `permission_read` deny at the detail endpoint (sub-cluster 4e) | GET `/api/<model>/<id>/` for a row whose `permission_read` returns `deny` → 200 with `{}` and no domain fields / `id` leakage. List endpoints already drop denied rows; this pins the serializer guard for guessed detail URLs. |

---

### 4m. `ApiKeyAwareLoginRequiredMiddleware` — instance API-key bypass ✅

**Gap:** PR #615 added a short-circuit to `ApiKeyAwareLoginRequiredMiddleware.check_login_required` so that requests authenticated with the instance API key (`is_instance_api_key_request`) bypass the OAuth2 login redirect, the same way DRF API-key requests already did. Without a regression gate, a refactor of the `or` chain (e.g. swapping evaluation order, mis-importing `is_instance_api_key_request`, or accidentally removing the branch) would silently revert all machine-to-machine requests to a login redirect with no framework-level signal.

**Scenario range:** 4.66 – 4.70. **Test file:** `lex/test_project/tests/permissions/test_4m_api_key_middleware.py`. **Type:** U. **Status:** ✅ Complete (Session 80 — June 18). Covers `lex/authentication/middleware.py`.

---

### 4x. Per-user Keycloak calls stay with their user; forged tokens and look-alike paths get no access ✅

**Gap:** three ways the permission layer could hand access to the wrong party. (1) python-keycloak's `uma_permissions()`/`userinfo()` put the caller's token on the process-wide connection before sending, so concurrent requests could receive another user's permissions and profile (BUG-038). (2) The bearer path accepted HS256 signed with the OIDC client secret, which every instance holds (BUG-035). (3) Login exemptions were unanchored regular expressions, so `/api/user_permissions/` and any path containing `/health` skipped sign-in (BUG-036). Per-user calls now go through `lex/api/keycloak/uma_client.py` with the token on that one request; `OIDC_RP_SIGN_ALGOS_ALLOWED = ["RS256"]`; every exemption is anchored.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.138 | the middleware under concurrency | 240 concurrent requests for 12 users: each carries only its own permissions, profile and roles |
| 4.139 | `KeycloakManager` under concurrency | 250 concurrent `get_uma_permissions` calls (the Streamlit and token-view path): each its own answer |
| 4.140 | `access_denied` | `[]` on the request and from `get_uma_permissions`; `set()` from `get_user_permissions` |
| 4.141 | token rejected | the request continues with `[]`/`{}`; `get_uma_permissions` → `None`; userinfo → `KeycloakUnauthorized` |
| 4.142 | Keycloak down | 502 HTML and unreachable: the request continues with the defaults; `KeycloakUnavailable`; no token in the logs |
| 4.143 | TLS and timeouts | `KEYCLOAK_VERIFY_SSL` (True, False, a CA path) and `(3.05, 10)` reach every call, with the caller's token on it |
| 4.144 | forged bearer token | HS256 signed with the client secret: no access and no user; RS256 from the IdP key: 200 |
| 4.145 | public endpoints | health, current user, active calculations, manifest and favicon (incl. `/api/api/...`) are not refused |
| 4.146 | look-alike paths | `/api/user_permissions/`, `/api/users/`, `.../healthx/...`, `.../health` are refused by the login middleware |
| 4.147 | anchors | every `OIDC_MIDDLEWARE_NO_AUTH_URL_PATTERNS` entry starts with `^` and ends with `$` or `(?:/\|$)` |

**Scenario range:** 4.138 – 4.147. **Test file:** `lex/test_project/tests/permissions/test_4x_keycloak_call_isolation.py`. **Type:** U (4.138–4.143), I (4.144–4.147). **Status:** ✅ Complete (2026-11-06). Batch: [4x](batches.md).
