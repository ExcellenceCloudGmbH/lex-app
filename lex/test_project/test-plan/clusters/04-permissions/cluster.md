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
| 4.4 | `permission_edit` restricts editable fields | PATCH to restricted field is rejected or ignored (BUG-010 resolved: 400 `EDIT_NOT_ALLOWED`) |
| 4.5 | `permission_delete` denies deletion | DELETE returns 403 |
| 4.6 | `permission_create` denies creation | POST returns 403 |
| 4.7 | Keycloak scope fallback — read scope present | Default `permission_read` allows all fields |
| 4.8 | Keycloak scope fallback — no scopes | Default `permission_read` denies |
| 4.9 | Legacy `can_read()` compatibility | `can_read(request)` returns same fields as `permission_read` |
| 4.10 | `UserContext.from_request` builds correct context | Groups, scopes, roles, email correctly populated |
| 4.11 | API key context | `client_roles` includes "api_key", scopes from key identity |
| 4.12 | `with_instance` resolves instance-specific Keycloak scopes | Scopes matched by `rsname` and `resource_set_id` |
| 4.40 | `permission_export` full deny at the export endpoint (sub-cluster 4h) | POST `/api/<model>/export` → 200 with rows present (read open) but every domain field blanked; only the framework's `{id, created_by, edited_by}` columns may carry data. Pins the union behaviour in `ModelExportView.get_exportable_fields_for_object` against both over-restrictive (rows dropped) and over-permissive (domain leaks) drift. |
| 4.41 | Full `permission_read` deny at the detail endpoint (sub-cluster 4e) | GET `/api/<model>/<id>/` for a row whose `permission_read` returns `deny` → 404 by default (LEX_SEC_NOT_FOUND); with `LEX_LEGACY_SECURITY=true` the old 200 with `{}` and no domain fields / `id` leakage. List endpoints already drop denied rows; this pins the serializer guard for guessed detail URLs. |

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

---

### 4o. Secure defaults on the generic REST endpoints ✅

**Gap:** permission methods were only enforced where the serializer happened to ask: a raising hook allowed, an unreadable record answered `200 {}`, `permission_edit` was ignored on writes (BUG-010), bulk delete and `run_step` skipped `permission_delete`/`permission_create`, and history, global search, export and file download ignored read permission. The hardening turns every one of these on by default (`lex/core/security_flags.py`; `LEX_LEGACY_SECURITY=true` or `LEX_SEC_<FLAG>=false` restore the old behaviour). No new denial answers 403 — the bundled frontend signs out on 403.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.80 | raising `permission_read` | the row is hidden; legacy lets it through and fails 500 |
| 4.81 | raising `permission_create` | the create is refused (500, nothing stored); legacy stores |
| 4.82 | unreadable record | 404; legacy `200 {}`; `LEX_SEC_NOT_FOUND=true` wins over the umbrella |
| 4.83 | PATCH/DELETE unreadable | 404, row unchanged |
| 4.84 | `/many` | GET drops unreadable ids (200); DELETE naming one answers 404 |
| 4.85 | disallowed field | 400 `{code: EDIT_NOT_ALLOWED, detail, error, fields}`; allowed field 200 |
| 4.86 | unchanged values | sending a disallowed field unchanged is not an edit |
| 4.87 | no edit right | every PATCH refused; legacy applies it |
| 4.88 | bulk PATCH | 400, nothing changed |
| 4.89 | bulk DELETE | `permission_delete` per row, 400 `DELETE_NOT_ALLOWED` with ids |
| 4.90 | ~~run_step~~ | **Retired 2026-10-22** — it pinned the ProcessFlow endpoint, removed with the Process feature; its absence is pinned by [10.91](../10-api_layer/cluster.md). The id is not reused. |
| 4.91 | history | 404 for an unreadable record |
| 4.92 | global search | only readable rows; legacy all |
| 4.93 | export | only readable rows |
| 4.94 | file download | 400 without params, 404 for non-file field or unreadable record, 200 bytes |
| 4.95 | HTML report | signed-in 200, anonymous not 200, unknown 404 |
| 4.96 | BUG-011 | `permission_read` once per row on a list |
| 4.97 | API key | the serializer decides a key's read with the key's own context |
| 4.98 | flags | defaults, umbrella, per-flag override, unknown name |
| 4.99 | `is_superuser_or_admin` | superuser or `admin` group only |
| 4.100 | signed out | the sign-in answer (302/401/403), never 404/200 |

**Scenario range:** 4.80 – 4.100. **Test file:** `lex/test_project/tests/permissions/test_4o_secure_defaults.py`. **Type:** E (4.98–4.99 U). **Status:** ✅ Complete (2026-10-18). Batch: [4o](batches.md).

---

### 4p. Internal tools, Django users, permission_list and history writes ✅

**Gap:** operator endpoints were open to every signed-in user: anyone could delete unused storage files, write to the server log, read every calculation log (and its PDF) and edit Django's users through the generic API — including making themselves superuser. A model's own `permission_list` was ignored by the list endpoint. History rows could be "corrected" by anyone (`One.update`'s bitemporal correction). All on by default; `LEX_LEGACY_SECURITY=true` / `LEX_SEC_INTERNAL|NOT_FOUND|HISTORY_WRITE=false` restore the old behaviour. No new 403.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.101 | delete-unused-files | plain user 404 and no cleanup; `admin` client role and superuser 200; legacy 200 |
| 4.102 | POST api/logs | plain user 404 with `error`, nothing logged; superuser and legacy 201 |
| 4.103 | calculation-log tree | readable record's calculation 1, hidden 0, record-less 0, unfiltered 0; superuser and legacy all |
| 4.104 | calculation-log PDF | readable 200; hidden, record-less and unknown 404; superuser 200 for the record-less log |
| 4.105 | `auth.User` writes | PATCH, create, bulk delete → 400 `EDIT_NOT_ALLOWED`, nothing changes; superuser PATCH 200 |
| 4.106 | `permission_list` false | list 404; legacy 200; allowing 200 |
| 4.107 | PATCH/PUT history row | 400 `EDIT_NOT_ALLOWED` with `error`, history unchanged; superuser 200 |
| 4.108 | other history writes | DELETE, POST, bulk PATCH/DELETE, meta-history PATCH → 400 `EDIT_NOT_ALLOWED`, never 403 |
| 4.109 | opt-out | `LEX_SEC_HISTORY_WRITE=false` applies the correction (200) |
| 4.110 | main record | PATCH of the record itself still 200 |

**Scenario range:** 4.101 – 4.110. **Test file:** `lex/test_project/tests/permissions/test_4p_internal_tools_and_history.py`. **Type:** E. **Status:** ✅ Complete (2026-10-18). Batch: [4p](batches.md).
