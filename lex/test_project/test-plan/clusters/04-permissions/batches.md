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
| Files covered | `lex/core/security_flags.py`, `lex/api/security.py`, `lex/api/views/model_entries/{One,Many,History,filter_backends}.py`, `lex/api/views/global_search_for_models/Search.py`, `lex/api/views/file_operations/{ModelExport,FileDownload}.py`, `lex/api/filters/GenericFilters.py`, `lex/api/serializers/base_serializers.py`, `lex/process_admin/views/model_relation_views.py` (`Overview`) |
| Test file | `lex/test_project/tests/permissions/test_4o_secure_defaults.py` |
| Test classes | `TestCluster04o_FailClosedAndNotFound` (4.80–4.84), `TestCluster04o_EditEnforcement` (4.85–4.89), `TestCluster04o_ReadSurfaces` (4.91–4.95), `TestCluster04o_SignedOut` (4.100), `TestCluster04o_ReadOncePerRow` (4.96), `TestCluster04o_ApiKeyReadContext` (4.97), `TestCluster04o_SecurityFlags` (4.98), `TestCluster04o_SuperuserOrAdmin` (4.99) |
| Fixtures | `RaisingReadItem`, `SecretDocument` (new in `permissions/models.py`), `ProtectedItem`, `FilterBackendItem`, `KeycloakItem`; `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **20 pass / 0 fail** — 4.90 retired 2026-10-22 with the ProcessFlow endpoint (see [10q](../10-api_layer/batches.md)); the id is not reused |
| Also changed | 4.4 live again (BUG-010 resolved); 4.41 asserts 404 by default and the old `200 {}` under `LEX_LEGACY_SECURITY=true`; 10.47–10.54 mocks report a real `FileField` and stand in for the read check; 12h `AttachmentItem` opens read/edit |
| Status | ✅ Complete — ported from #816 without `permission_read_queryset`, `permission_serializer`, `HTMLReport.permission_view`, `lex_strict_input`, `EXPOSE_AUTO_SERIALIZER` |

---

### Batch 4p — Internal tools, Django users, permission_list and history writes ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.101 – 4.110 |
| Type | E |
| Files covered | `lex/core/security_flags.py` (`is_superuser`, `superuser_only`), `lex/api/security.py` (`write_denial`, `list_denied`, `is_history_model`), `lex/api/views/sharepoint/DeleteUnusedFiles.py`, `lex/api/views/LexLoggerView/LexLoggerView.py`, `lex/api/views/model_entries/CalculationLogTreeView.py`, `lex/api/views/calculations/DownloadMarkdownPdf.py`, `lex/api/views/model_entries/{One,Many,List}.py`, `lex/api/views/model_entries/mixins/DestroyOneWithPayloadMixin.py` |
| Test file | `lex/test_project/tests/permissions/test_4p_internal_tools_and_history.py` |
| Test classes | `TestCluster04p_InternalTools` (4.101–4.105), `TestCluster04p_PermissionList` (4.106), `TestCluster04p_HistoryWrites` (4.107–4.110) |
| Fixtures | `SECURE_DEFAULT_MODELS` (`ProtectedItem`, `FilterBackendItem`), `CalculationLog` rows with and without a record, `auth.User`; SharePoint cleanup, `LexLogger` and PDF rendering stubbed; `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **10 pass / 0 fail** — 2026-10-22: 4.105 creates through the CRUD endpoint and 4.108 dropped its `run_step` probe, the ProcessFlow endpoint having been removed (see [10q](../10-api_layer/batches.md)) |
| Also changed | 10.61–10.66 drive the tree as a superuser and 15.32–15.38 download as one: their logs belong to no record, which only operators may read now |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-18-v2-security-hardening-internal-config.md) |

---

### Batch 4r — a raising permission hook denies on the remaining surfaces ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.116 – 4.120 |
| Type | E (4.118, 4.120: I) |
| Files covered | `lex/api/utils/helpers.py` (`can_read_from_payload`), `lex/api/serializers/base_serializers.py` (`_can_read_related_payload_reference`, audit-log payload filtering), `lex/api/views/file_operations/ModelExport.py` (`get_exportable_fields_for_object`), `lex/api/views/model_entries/mixins/PermissionAwareSerializerMixin.py` (`run_validation`) |
| Test file | `lex/test_project/tests/permissions/test_4r_fail_closed_remaining_surfaces.py` |
| Test classes | `TestCluster04r_AuditLogRead` (4.116–4.118), `TestCluster04r_Export` (4.119), `TestCluster04r_SerializerMixin` (4.120) |
| Fixtures | `RaisingParent`, `RaisingChild`, `RaisingExportItem` (new in `permissions/models.py`), `RaisingReadItem`; `AuditLog` rows written directly; `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **5 pass / 0 fail** — all five fail without the change |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-23-fail-closed-remaining-surfaces.md) |

---

### Batch 4q — permission_create on the filled record ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.111 – 4.115 |
| Type | E |
| Files covered | `lex/core/security_flags.py` (`CREATE`), `lex/api/security.py` (`filled_record`, `ask_create`), `lex/api/views/model_entries/One.py` (`create`, `perform_create`, `_check_create_on_filled_record`) |
| Test file | `lex/test_project/tests/permissions/test_4q_create_on_filled_record.py` |
| Test classes | `TestCluster04q_CreateOnFilledRecord` (4.111–4.115) |
| Fixtures | `CreateParent`, `ScopedCreateItem` (new in `permissions/models.py`); `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **5 pass / 0 fail** — 4.111–4.114 fail on lex-app-v2 `0ac01da8`, 4.115 (opt-out) passes on both |
| Also changed | 4.98 lists the tenth flag |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-23-create-on-filled-record.md) |

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
| Also changed | `test_4g_user_permissions_api.py` 4.32/4.32b and `api_layer/test_10o_error_and_route_hardening.py` 10.90: their fake manager answers `get_userinfo`, because the middleware no longer reaches into the shared python-keycloak connection (`km.oidc`). Intent unchanged. |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-auth-hotfixes.md) |

**Allocation note:** Letter x, not s-w: those are in flight on feat/multi-value-filters (s, t) and the feat/ext-* branches (s-w, scenarios up to 4.137). Same letter and IDs on lex-app-v2 and release/2.1.x.

---

### Batch 4aa — the Streamlit token carries the caller's own Keycloak permissions ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.163 – 4.163 |
| Type | U |
| Files covered | `lex/authentication/views/token_views.py` (`StreamlitTokenView._get_user_permissions`) |
| Test file | `lex/test_project/tests/permissions/test_4aa_streamlit_token_permissions.py` |
| Test classes | `TestCluster04aa_StreamlitTokenPermissions` (4.163) |
| Fixtures | none — `RequestFactory` requests; `KeycloakManager` patched |
| Tests landed | **1 pass / 0 fail** — Mutation: with the old relative import, 4.163 fails |
| Status | ✅ Complete |

**Allocation note:** Letter aa and 4.163: 4y (4.148–4.158) is lex-app #869's and 4z (to 4.162) is #880's.

---


---

### Batch 4z — A schedule can do no more than the person who saved it ✅

| Property | Value |
| --- | --- |
| Scenario range | 4.159 – 4.162 |
| Type | E |
| Files covered | `lex/scheduling/models.py` (`_calculate_denial`, `validate_definition`, `_real_user`) |
| Test file | `lex/test_project/tests/permissions/test_4z_scheduled_job_permissions.py` |
| Test classes | `TestCluster04z_ScheduledJobPermissions` (4.159–4.162) |
| Fixtures | `ScheduledDeniedCalc` (readable, never editable), `ScheduledJobTestCase.scheduled_jobs_allowed()` |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete (2026-11-10) — scheduled jobs, paired with the other five scheduling batches (8f, 7x, 10ac, 6v, 4z, 1bn) |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 4.159 | a forbidden record | 400 on `target_model` saying the caller may not calculate it; nothing stored |
| 4.160 | every record of a forbidden model | 400 on `target_model`; nothing stored |
| 4.161 | code is trusted | a schedule created without a request for the same model is stored and scheduled |
| 4.162 | no access to schedules | creating one without the grant is refused (401/403); nothing stored |

**Why a regression matters:** Anyone with access to schedules could calculate records they could never calculate themselves.

**Allocation note:** Allocated past the letters and scenario IDs that in-flight branches (feat/ext-*, feat/auth-long-sessions) already hold: the first letter absent from lex-app-v2 and every remote branch, and a range above the highest max_scenario on any branch, so neither side has to renumber on merge. Design: [`docs/superpowers/plans/2026-11-10-scheduled-jobs.md`](../../../../../docs/superpowers/plans/2026-11-10-scheduled-jobs.md).
