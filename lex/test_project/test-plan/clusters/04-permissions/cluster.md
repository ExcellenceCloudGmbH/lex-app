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

### 4n. Keycloak UMA permissions in Reflex dashboards ✅

**Gap:** a Streamlit dashboard reads the user's Keycloak UMA permissions from `st.session_state.permissions`, filled by the proxy. A Reflex dashboard has no proxy: `current_permissions(state)` fetches them through `KeycloakManager` for the session's access token, and `has_permission(Model, scope)` turns one into a Reflex Enterprise `auth=` check. Getting the cache wrong in one direction leaks a previous user's grants into a session; in the other, it asks Keycloak on every event or demotes a user whenever Keycloak blips.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.75 | one lookup per access token | asked once per token, again after a refresh; no token, no lookup; the session keeps a digest, never the token |
| 4.76 | failures degrade safely | the same user keeps their grants while Keycloak fails; another user never inherits them |
| 4.77 | sign-out forgets them | `_reset_auth` clears the grants with the tokens |
| 4.78 | `has_permission` | model-wide grants of the scope on `<app_label>.<ModelName>` only; fails closed |
| 4.79 | the access token | any state reaches the session's token and permissions |

**Scenario range:** 4.75 – 4.79. **Test file:** `lex/test_project/tests/permissions/test_4n_reflex_permissions.py`. **Type:** U. **Status:** ✅ Complete (2026-09-28). Covers `lex/lex_app/reflex/auth.py`. Batch: [4n](batches.md).

---

### 4o. Generic API gate and OIDC redirect guard ✅

**What it tests:** GENERIC_API all/superuser/off decides access to lex's generic api/* (404 when closed) without touching the project API or the health check; next/fail on /oidc/* must be relative, the site's origin or a trusted origin (CSRF_TRUSTED_ORIGINS wildcards included), with the legacy opt-outs.

**Why a regression matters:** A project serving its own API would otherwise expose every column of every model through the generic endpoints; the OIDC views were an open redirect.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.80 | gate modes | all/superuser/off per path and user; project prefix and api/health stay open |
| 4.81 | redirect targets | relative, own, CORS and wildcard origins pass; foreign, protocol-relative, javascript: get 400 |
| 4.82 | redirect opt-out | LEX_SEC_OIDC_REDIRECT=false and legacy pass |
| 4.83 | default gate wiring | regular user gets 200 on api/model-structure |
| 4.84 | superuser gate wiring | 404 NOT_FOUND for a regular user, 200 for a superuser, health 200 |
| 4.85 | guard wiring | foreign next answers 400 REDIRECT_NOT_ALLOWED before the OIDC view |

**Scenario range:** 4.80-4.85. **Test file:** `lex/test_project/tests/permissions/test_4o_generic_api_gate_and_redirects.py`. **Type:** U + E. **Status:** ✅ Complete (2026-10-16). Covers `lex/api/middleware/generic_api_gate.py`, `lex/authentication/middleware.py` (OidcRedirectGuardMiddleware), MIDDLEWARE in `lex/lex_app/settings.py`. Batch: [4o](batches.md).

---

### 4p. Secure permission defaults on the generic REST API ✅

**What it tests:** FAIL_CLOSED, NOT_FOUND, EDIT and READ_SURFACES on every endpoint they apply to, each with its legacy opt-out where behaviour changed; permission_read_queryset deciding rows in SQL; one permission_read evaluation per listed row.

**Why a regression matters:** A permission method is only as good as its enforcement: before 3.0 a raising check allowed, PATCH ignored permission_edit, bulk delete ignored permission_delete, and history, search, export and downloads ignored permission_read.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.86 | raising read check hides its row | list shows only the healthy row; legacy 500 (pre-3.0) |
| 4.87 | raising create check refuses | 500, nothing stored; legacy stores |
| 4.88 | unreadable record not found | 404; legacy 200 {} |
| 4.89 | unreadable record not changeable | PATCH and DELETE 404, row unchanged |
| 4.90 | bulk respects visibility | GET drops the hidden row; DELETE 404, nothing deleted |
| 4.91 | disallowed field | 400 EDIT_NOT_ALLOWED naming the field; allowed field 200 |
| 4.92 | unchanged values not an edit | 200 |
| 4.93 | record without edit right | 400; legacy applies |
| 4.94 | bulk PATCH | 400, nothing changed |
| 4.95 | bulk DELETE | 400 DELETE_NOT_ALLOWED with ids; legacy deletes |
| 4.96 | run_step | PATCH 400 EDIT_NOT_ALLOWED; create 400 CREATE_NOT_ALLOWED |
| 4.97 | history of unreadable record | 404; readable 200 |
| 4.98 | global search | only readable rows; legacy both |
| 4.99 | export | two of three rows |
| 4.100 | file download | 400 without params, 404 non-file field, 404 hidden record, 200 bytes |
| 4.101 | HTMLReport.permission_view | 404 regular, 200 superuser, anonymous refused, unknown 404 |
| 4.102 | permission_read_queryset | only public rows listed; private detail 404 |
| 4.103 | one evaluation per row | three rows, three permission_read calls |

**Scenario range:** 4.86-4.103. **Test file:** `lex/test_project/tests/permissions/test_4p_secure_defaults.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/api/security.py`, `lex/api/views/model_entries/{One,Many,History,filter_backends}.py`, `lex/api/views/process_flow/CreateOrUpdate.py`, `lex/api/views/global_search_for_models/Search.py`, `lex/api/views/file_operations/{ModelExport,FileDownload}.py`, `lex/api/filters/GenericFilters.py`, `lex/process_admin/views/model_relation_views.py` (Overview), `lex/core/models/{LexModel,HTMLReport}.py`, `lex/api/serializers/base_serializers.py`. Batch: [4p](batches.md).

---

### 4q. Scoped API keys (LexAPIKey) ✅

**What it tests:** A scoped key's scopes drive the default permission methods; requests run as the key's user (created_by names the user, no CSRF needed); keys work only below their path prefixes; revoked and expired keys are ignored; extra header names and a project resolver work; classic keys keep their behaviour.

**Why a regression matters:** A classic key is an all-scope identity on every model; an integration key must be limited to what its integration needs.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.104 | scopes drive permissions | read key sees the row, list-only key none |
| 4.105 | acts as its user | create without CSRF; created_by names the service user |
| 4.106 | path prefixes | 404 outside api/elsewhere/ |
| 4.107 | revoked/expired | no data |
| 4.108 | extra headers | X-Project-Key authenticates |
| 4.109 | resolver | custom- keys resolve to the resolver's user |
| 4.110 | classic key unchanged | detail 200 with data |

**Scenario range:** 4.104-4.110. **Test file:** `lex/test_project/tests/permissions/test_4q_scoped_api_keys.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/authentication/models/_api_keys.py`, migration 0002_lexapikey, `lex/api/utils/api_key_requests.py`, `lex/authentication/middleware.py`, `lex/authentication/authentication_backends/api_keys.py`, `lex/api/middleware/keycloak_permissions.py`, `lex/core/models/LexModel.py` ('*' scope grant), `filter_backends.py`. Batch: [4q](batches.md).

---

### 4r. Audit authors for users without names ✅

**What it tests:** A nameless key user is the audit author by username on a single create and on a bulk delete; a named user is still recorded as first and last name.

**Why a regression matters:** BUG-007: audit rows of service accounts named nobody (" "), so nobody could tell who changed the data.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.111 | nameless user on create | author is the username |
| 4.112 | nameless user on bulk delete | both rows name the username |
| 4.113 | named user | author is "Ada Lovelace" as before |

**Scenario range:** 4.111-4.113. **Test file:** `lex/test_project/tests/permissions/test_4r_audit_authors.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Covers `lex/audit_logging/mixins/AuditLogMixin.py` (log_change author), `lex/audit_logging/mixins/BulkAuditLogMixin.py` (log_change author, _audit_author). Batch: [4r](batches.md).

### 4s. API-key read decided by the serializer ✅

**What it tests:** A classic API key reading a record whose visibility the serializer decides itself gets the record, not an empty body.

**Why a regression matters:** Integrations on classic keys would read empty records in legacy mode and in project views.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.114 | classic key read decided by the serializer | 200 with the label |

**Scenario range:** 4.114. **Test file:** `lex/test_project/tests/permissions/test_4s_api_key_read_context.py`. **Type:** E. **Status:** ✅ Complete (2026-10-17). Covers `lex/api/serializers/base_serializers.py` (_get_can_read_context). Batch: [4s](batches.md).

---
