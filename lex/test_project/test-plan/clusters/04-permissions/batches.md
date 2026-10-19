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

### Batch 4o — Secure defaults on the generic REST endpoints ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.80 – 4.100 |
| Type | E (4.98–4.99 U) |
| Files covered | `lex/core/security_flags.py`, `lex/api/security.py`, `lex/api/views/model_entries/{One,Many,History,filter_backends}.py`, `lex/api/views/process_flow/CreateOrUpdate.py`, `lex/api/views/global_search_for_models/Search.py`, `lex/api/views/file_operations/{ModelExport,FileDownload}.py`, `lex/api/filters/GenericFilters.py`, `lex/api/serializers/base_serializers.py`, `lex/process_admin/views/model_relation_views.py` (`Overview`) |
| Test file | `lex/test_project/tests/permissions/test_4o_secure_defaults.py` |
| Test classes | `TestCluster04o_FailClosedAndNotFound` (4.80–4.84), `TestCluster04o_EditEnforcement` (4.85–4.90), `TestCluster04o_ReadSurfaces` (4.91–4.95), `TestCluster04o_SignedOut` (4.100), `TestCluster04o_ReadOncePerRow` (4.96), `TestCluster04o_ApiKeyReadContext` (4.97), `TestCluster04o_SecurityFlags` (4.98), `TestCluster04o_SuperuserOrAdmin` (4.99) |
| Fixtures | `RaisingReadItem`, `SecretDocument` (new in `permissions/models.py`), `ProtectedItem`, `FilterBackendItem`, `KeycloakItem`; `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **21 pass / 0 fail** |
| Also changed | 4.4 live again (BUG-010 resolved); 4.41 asserts 404 by default and the old `200 {}` under `LEX_LEGACY_SECURITY=true`; 10.47–10.54 mocks report a real `FileField` and stand in for the read check; 12h `AttachmentItem` opens read/edit |
| Status | ✅ Complete — ported from #816 without `permission_read_queryset`, `permission_serializer`, `HTMLReport.permission_view`, `lex_strict_input`, `EXPOSE_AUTO_SERIALIZER` |

---

### Batch 4p — Internal tools, Django users, permission_list and history writes ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.101 – 4.110 |
| Type | E |
| Files covered | `lex/core/security_flags.py` (`is_superuser`, `superuser_only`), `lex/api/security.py` (`write_denial`, `list_denied`, `is_history_model`), `lex/api/views/sharepoint/DeleteUnusedFiles.py`, `lex/api/views/LexLoggerView/LexLoggerView.py`, `lex/api/views/model_entries/CalculationLogTreeView.py`, `lex/api/views/calculations/DownloadMarkdownPdf.py`, `lex/api/views/model_entries/{One,Many,List}.py`, `lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py`, `lex/api/views/process_flow/CreateOrUpdate.py` |
| Test file | `lex/test_project/tests/permissions/test_4p_internal_tools_and_history.py` |
| Test classes | `TestCluster04p_InternalTools` (4.101–4.105), `TestCluster04p_PermissionList` (4.106), `TestCluster04p_HistoryWrites` (4.107–4.110) |
| Fixtures | `SECURE_DEFAULT_MODELS` (`ProtectedItem`, `FilterBackendItem`), `CalculationLog` rows with and without a record, `auth.User`; SharePoint cleanup, `LexLogger` and PDF rendering stubbed; `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **10 pass / 0 fail** |
| Also changed | 10.61–10.66 drive the tree as a superuser and 15.32–15.38 download as one: their logs belong to no record, which only operators may read now |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-18-v2-security-hardening-internal-config.md) |

---

## Batch 4q — filter_values honours row and field read permissions (4.111-4.118)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/FilterValues.py`, `lex/api/utils/filterable_fields.py`
  (`value_list_field`, `renders_rows_as_stored`, `_renders_value_as_stored`),
  `lex/api/serializers/base_serializers.py` (`_wrap_custom_serializer`'s `_KEEPS_FIELD_VALUES` mark),
  `lex/api/views/model_entries/filter_backends.py` (the read filter 4.112 and 4.118 rely on)
- **Test file:** `lex/test_project/tests/permissions/test_4q_filter_values_permissions.py`
- **Test classes:** `TestCluster04q_FilterValuesPermissions`
- **Fixtures:** new `RowFieldItem`; `FieldLevelItem`; `KeycloakItem` (4.118); `OwnedItem` (4.116's nested-
  serializer subtest, with a per-record Keycloak grant like 4.118's); serializers registered on
  `RowFieldItem.api_serializers` for one test each (4.115-4.117), and on `OwnedItem.api_serializers` for
  the 4.116 nested-serializer subtest
- **Status:** complete — 8 pass
- **Final review (4.115-4.118):** a write-only field, a field class with its own `get_attribute` or
  `to_representation`, a nested serializer on an FK column, and every field of a serializer that
  rewrites its rows are refused with the standard 400; on the database path a record-level grant
  limits the count to that record. Mutation: the read filter replaced by the identity fails 4.118 alone.

---
