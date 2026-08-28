## Cluster 1 — Init / Project Bootstrap (existing 1a–1n + new 1o)

> **Renumbering note (May 12):** the plan's original placeholder names (1d/1e/1f) collided with sub-clusters that already shipped (1d–1n exist). The next free letter is **1o**, the next free scenario ID is **1.110**. Future batches in this cluster: **1p**, **1q**, **1r**.

### Batch 1o — Lazy imports + sync-exclusion + history-config helpers ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.110 – 1.124 |
| Type | U |
| Files covered | `lex/process_admin/__init__.py`, `lex/lex_app/__init__.py`, `lex/lex_app/keycloak_exclusions.py`, `lex/lex_app/simple_history_config.py` |
| Test file | `lex/test_project/tests/init/test_1o_lazy_imports_and_helpers.py` |
| Test classes | `TestCluster01o_ProcessAdminLazyGetattr`, `TestCluster01o_KeycloakExclusions`, `TestCluster01o_SimpleHistoryConfig`, `TestCluster01o_LexAppPackageAlias` |
| Fixtures | none (synthetic models built with `type()` + `types.SimpleNamespace`) |
| Tests landed | **15 pass / 0 fail in 0.001s** |
| Coverage gain | +0.4 % (estimated; measured on next coverage run) |
| Status |  Complete (Session 53 — May 12) |

### Batch 1p — Settings / config / URLs / top-level views ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.125 – 1.146 |
| Type | U + I |
| Files covered | `lex/lex_app/settings.py`, `lex/lex_app/urls.py`, `lex/lex_app/views.py`, `lex/utilities/config/generic_app_config.py`, `lex/core/config.py` |
| Test file | `lex/test_project/tests/init/test_1p_settings_urls_views.py` |
| Test classes | `TestCluster01p_SettingsConstants`, `TestCluster01p_UrlConfResolves`, `TestCluster01p_HealthEndpoint`, `TestCluster01p_LexProjectConfig`, `TestCluster01p_GenericAppConfigHelpers` |
| Fixtures | `tempfile.TemporaryDirectory` for `lex_config.py` writing; no new models |
| Tests landed | **22 pass / 0 fail in 0.016s** |
| Coverage gain | +0.6 % (estimated; measured on next coverage run) |
| Status |  Complete (Session 54 — May 12). Note: `lex/lex_app/apps.py` AppConfig.ready surface deferred to **1q** — it requires real bootstrap fixtures. |

### Batch 1q — Migration file completeness release gate ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.147 – 1.147 |
| Type | U |
| Files covered | `lex/lex_app/migrations/*.py`, `lex/authentication/migrations/*.py`, `lex/audit_logging/migrations/*.py`, `lex/legacy_data/migrations/*.py` |
| Test file | `lex/test_project/tests/init/test_1q_migration_files_complete.py` |
| Test classes | `TestCluster01q_MigrationFilesComplete` |
| Fixtures | none |
| Tests landed | **1 pass / 0 fail in 2.42s** |
| Coverage gain | n/a (release-gate drift test) |
| Status | ✅ Complete (Session 70 — June 2) |

### Batch 1s — Log-noise cleanup + lex-namespace debug control (EXC-1787) ✅

> **Letter note (June 8):** letter **1r** was already taken on disk by an in-flight,
> untracked batch (`test_1r_lex_view_embed_helper.py`, Streamlit `lex_view` embed
> helper, scenarios up to 1.158) that is not yet documented in this plan. Per the
> never-renumber rule, this batch took the next free letter **1s** and the next free
> scenario ID after 1.158 → **1.159**.

| Property | Value |
| --- | --- |
| Scenario range | 1.159 – 1.168 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (urllib3 `InsecureRequestWarning` suppression gate `LEX_SUPPRESS_INSECURE_WARNING`; new `LEX_LOG_LEVEL` + `lex` logger entry; `CONSOLE_HANDLER_LEVEL` = `min(CONSOLE_LEVEL, LEX_LOG_LEVEL)` derivation; blanket `LEX_SUPPRESS_WARNINGS` → `warnings.filterwarnings("ignore")` gate) |
| Test file | `lex/test_project/tests/init/test_1s_log_cleanup_and_lex_debug.py` |
| Test classes | `TestCluster01s_InsecureWarningSuppression` (1.159 default-suppressed, 1.160 opt-out honoured, 1.161 opt-out case-insensitive), `TestCluster01s_LexNamespaceDebugLevel` (1.162 lex logger defaults INFO + propagate False, 1.163 `LEX_LOG_LEVEL=DEBUG` raises lex only while root stays INFO, 1.164 console handler drops to DEBUG for lex, 1.165 console handler stays INFO by default), `TestCluster01s_BlanketWarningSuppression` (1.166 default installs `filterwarnings("ignore")`, 1.167 opt-out skips it, 1.168 opt-out case-insensitive) |
| Fixtures | none (reloads `lex.lex_app.settings` under patched `os.environ`; `sentry_sdk.init` mocked across reloads; env restored in cleanup) |
| Tests landed | **10 pass / 0 fail in 0.26s** |
| Coverage gain | negligible (settings is import-time; pins env-var-driven branches) |
| Status | ✅ Complete (Session 75 — June 8) |

### Batch 1t — `DISABLE_SERVER_SIDE_CURSORS` placement (production cursor crash) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.169 – 1.170 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (the flag was declared at module level, where Django ignores it; moved into each PostgreSQL `DATABASES` alias config dict — the only place `connection.settings_dict` reads it — so server-side cursors are actually disabled behind the `cloud-sql-proxy`/pgbouncer transaction-pooling proxy that otherwise causes `InvalidCursorName` on every `.iterator()`) |
| Test file | `lex/test_project/tests/init/test_1t_disable_server_side_cursors.py` |
| Test classes | `TestCluster01t_DisableServerSideCursors` (1.169 every Postgres alias carries the flag in its config dict, 1.170 the live `connections["default"].settings_dict` honours it on Postgres / engine-gated for SQLite) |
| Fixtures | none (introspects `settings.DATABASES` + `django.db.connections`) |
| Tests landed | **2 pass / 0 fail in 0.09s** |
| Coverage gain | negligible (settings is import-time; pins a config-placement contract) |
| Status | ✅ Complete (Session 78 — June 9) |

### Batch 1u — Fast ASGI health/readiness probes (coverage task #620) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.171 – 1.175 |
| Type | U |
| Files covered | `lex/lex_app/fast_health.py`, `lex/lex_app/asgi.py` |
| Test file | `lex/test_project/tests/init/test_1u_fast_health_asgi.py` |
| Test classes | `TestCluster01u_FastHealthAsgi` (1.171 path helpers separate liveness/readiness, 1.172 health app drains request body and returns static Healthy payload, 1.173 readiness returns 200/503 based on DB readiness seam, 1.174 top-level HTTP ASGI app short-circuits probe paths before Django, 1.175 non-probe HTTP delegates to Django) |
| Fixtures | none — ASGI `receive`/`send` callables and `AsyncMock` seams only |
| Tests landed | **5 pass / 0 fail** (direct pytest) |
| Coverage gain | `fast_health.py` path helpers + health/readiness ASGI apps; `asgi.py` `http_application` health/readiness/Django routing branches |
| Status | ✅ Complete (Session 81 — June 18). `python -m lex pytest ...` blocked locally by no PostgreSQL service; pure U tests pass with `DJANGO_SETTINGS_MODULE=lex_app.settings python -m pytest ...`. |

---

### Batch 1x — Health exposes encrypted runtime metadata for the Instance Controller ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.187 – 1.194 |
| Type | U + I |
| Files covered | `lex/lex_app/runtime_health.py`, `lex/lex_app/fast_health.py`, `lex/lex_app/views.py` |
| Test file | `lex/test_project/tests/init/test_1x_runtime_health_metadata.py` |
| Test classes | `TestCluster01x_RuntimeHealthMetadata` (1.187 missing key → legacy payload, 1.188 deployed pod adds encrypted runtime token, 1.189 token is ciphertext not plaintext, 1.190 wrong key can't decrypt, 1.191 encryption failure never breaks health, 1.192 Django health route uses runtime payload, 1.193 fast ASGI health route uses runtime payload, 1.194 missing COMMIT_SHA marked unknown) |
| Fixtures | none — Fernet round-trip + `patch.dict` env seams |
| Status | ✅ Complete. **Renumbered 2026-07-07 (BUG-023):** this file previously shared letter `1u` and IDs 1.171–1.175 with `test_1u_fast_health_asgi.py`; moved to fresh letter `x` + fresh IDs 1.187–1.194 (old 1.171→1.187 … 1.178→1.194). No logic change. |

---

### Batch 1v — `TIME_ZONE`↔`USE_TZ` coupling for `django_celery_beat` DatabaseScheduler ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.179 – 1.183 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (USE_TZ↔TIME_ZONE coupling); guards the `django_celery_beat` `is_due` path |
| Test file | `lex/test_project/tests/init/test_1v_scheduler_tz_invariant.py` |
| Test classes | `TestCluster01v_TimezoneInvariant` (1.179 `USE_TZ=False ⟹ TIME_ZONE=="UTC"`, 1.180 `timezone.now()` naive frame within seconds of real UTC, 1.181 recovery `IntervalSchedule` due in live frame via `ModelEntry.is_due` replica, 1.182 future-edit `clocked(now+30s)` ~30s away not hours + past due, 1.183 naive-UTC round-trips exact vs naive-Berlin misread ≥3600s) |
| Fixtures | none — `celery.schedules.schedule` / `django_celery_beat.clockedschedule.clocked` against `lex.lex_app.celery.app` |
| Tests landed | **5 pass / 0 fail** (direct pytest) |
| Coverage gain | settings-level `TIME_ZONE` coupling under `USE_TZ=False`; pins the `maybe_make_aware` naive-as-UTC read for both the recovery interval sweep and future-edit clocked schedule |
| Status | ✅ Complete (Session 84 — June 26). Regression: history+init+settings 63 pass / 1 skip; celery_async+audit_logging 262 pass / 4 skip / 1 xfail. |

---

### Batch 1w — `LEX_TASK_RECOVERY_ENABLED` defaults OFF (stuck calc resets on restart) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.184 – 1.186 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (`LEX_TASK_RECOVERY_ENABLED` default flipped `true` → `false`) |
| Test file | `lex/test_project/tests/init/test_1w_recovery_default_deployment_target.py` |
| Test classes | `TestCluster01w_RecoveryDefaultOff` (1.184 env unset ⟹ `False`; 1.185 explicit `=true` ⟹ `True` opt-in; 1.186 explicit `=false` ⟹ `False` + case-insensitive `TRUE` ⟹ `True`) |
| Fixtures | none — env-patch + `importlib.reload(lex.lex_app.settings)` harness (mirrors 1s), `sentry_sdk.init` mocked per reload |
| Tests landed | **3 pass / 0 fail** (direct pytest) |
| Coverage gain | settings-level recovery master-switch default resolution |
| Status | ✅ Complete (Session 90 — July 1). Default OFF keeps the startup sweep in blind-abort mode so a stuck `IN_PROGRESS` row is reset on restart when no recovery-supervisor pod runs (local/CI/un-provisioned deploys); prod opts back in explicitly. Verified nested-dispatch untouched: 7j/7q/8ab all pass. Pre-existing unrelated `test_15d` logging-chain failures reproduce identically with the old `=true` default. |

---

---

### Batch 1i — `rebase_incident_datetimes` maintenance command ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.195 – 1.200 |
| Type | E |
| Files covered | `lex/lex_app/management/commands/rebase_incident_datetimes.py` (new) |
| Test file | `lex/test_project/tests/init/test_1i_rebase_incident_datetimes.py` |
| Test model | `lex/test_project/tests/init/models.py` → `IncidentDatetimeItem` (user `event_at` + managed `created_at`/`edited_at`) |
| Test classes | `TestCluster01i_RebaseIncidentDatetimes` (1.195 `--apply` re-anchors in-window value & spares `created_at`; 1.196 dry-run writes nothing; 1.197 **pre-upgrade** row untouched — late-upgrader safety; 1.198 **post-fix** row untouched — window upper bound; 1.199 **DST-aware** winter value shifts −1h not −2h; 1.200 **DST-transition** value flagged for review) |
| Fixtures | none — seeds rows via `.update()` to stamp `created_at`/`event_at` directly |
| Tests landed | **6 pass / 0 fail** |
| Coverage gain | incident data-migration command (dry-run/apply, per-instance `[--cutoff, --until)` window, app-stamped exclusion, ambiguous-row + DST-transition reporting, DST-aware correction) |
| Status | ✅ Complete — ships with the `USE_TZ=True` cutover. Corrects only user-entered datetimes created in the **per-instance** window `[--cutoff, --until)` — `--cutoff` (that instance's rc212 upgrade) is **required**, no global default, because too-early over-corrects correct pre-upgrade rows; `--until` (that instance's aware-UTC fix, default now) stops post-fix correct rows being re-shifted. Framework-managed timestamps and out-of-window rows are provably left alone. PostgreSQL-only (`AT TIME ZONE`); not idempotent (run once per instance). |

---

---

### Batch 1r — Fetched datetimes return in the DB-session display zone ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.201 – 1.202 |
| Type | I |
| Files covered | `lex/lex_app/settings.py` (`DATABASES['default']['TIME_ZONE'] = TIME_ZONE`, Postgres-guarded) |
| Test file | `lex/test_project/tests/init/test_1r_fetched_datetime_zone.py` |
| Test model | reuses `IncidentDatetimeItem` (`event_at`) |
| Test classes | `TestCluster01r_FetchedDatetimeZone` (1.201 fetched value carries the display-zone offset & preserves the instant; 1.202 Berlin wall-clock reads 11:00 with no `localtime()`) |
| Fixtures | none |
| Tests landed | **2 pass / 0 fail** (stable over repeated runs) |
| Coverage gain | DB-session display zone on reads (the zero-refactor fix for UTC-looking `str()`/labels) |
| Status | ✅ Complete — running the Postgres session in `TIME_ZONE` makes every fetched `DateTimeField` come back aware-Berlin (`11:00+02:00`) instead of UTC, so `str()`, `.date()`, `.hour`, and model `__str__` render local **with no per-field or per-model changes** — storage stays UTC, instant unchanged. Django's date-part lookups inject an explicit `AT TIME ZONE`, so bitemporal/`as_of` raw SQL is unaffected (verified: history + serializers + init + exports **385 pass** with it live; calc/audit/crud/api **457 pass**, the one api_layer failure is a pre-existing ordering artifact that passes in isolation with or without this change). |

---

### Batch 1y — IDE-aware setup run configurations ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.203 – 1.210 |
| Type | U |
| Files covered | `generate_pycharm_configs.py`, `lex/bin/lex.py`, `pyproject.toml` |
| Test file | `lex/test_project/tests/init/test_1y_ide_run_configs.py` |
| Test classes | `TestCluster01y_IdeRunConfigurations` |
| Fixtures | `tempfile.TemporaryDirectory`, Click `CliRunner`, controlled IDE environment markers; no database models |
| Tests landed | **8 pass / 0 fail, 10 subtests pass**; setup regression (`1a` + `1m`) **13 pass / 0 fail, 9 subtests pass** |
| Coverage gain | n/a — scaffolding module is outside configured `source = lex` and `lex/bin/lex.py` is explicitly omitted; tests pin IDE selection/fallback, VS Code parity, JSONC merge, idempotency, and setup output paths |
| Status | ✅ Complete — clear VS Code/PyCharm sessions generate their native format; unknown or conflicting sessions generate both; existing VS Code entries survive regeneration. See [2026-07-23 session](../../progress/sessions/2026-07-23-ide-run-configs.md). |

---

### Batch 1z — Streamlit auth proxy: iframe re-auth breakout (refused-to-connect fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.211 – 1.216 |
| Type | U |
| Files covered | `lex/proxy.py` (`_unauthenticated_response`, `_is_iframe_document_request`) |
| Test file | `lex/test_project/tests/init/test_1z_proxy_iframe_breakout.py` |
| Test classes | `TestCluster01z_ProxyIframeBreakout` (1.211 iframe → 401 frame-breakout not IdP redirect, 1.212 `<frame>` also breaks out, 1.213 top-level HTML still redirects to `/auth/login`, 1.214 no `Sec-Fetch-*` keeps redirect, 1.215 non-HTML → 401 JSON, 1.216 `_is_iframe_document_request` case-insensitive + scoped) |
| Fixtures | none — minimal ASGI `Request` builder + `patch.object(proxy, "PUBLIC_URL", "")` |
| Tests landed | **6 pass / 0 fail** (`python -m lex pytest`) |
| Coverage gain | proxy deny-branch routing: `Sec-Fetch-Dest: iframe`/`frame` document loads break out to a top-level login instead of redirecting the frame into Keycloak's un-frameable login page (`frame-ancestors 'self'` → "refused to connect"); the top-level redirect and API-401 paths stay unchanged |
| Status | ✅ Complete (2026-07-21). Root cause: the embedded `auth_token` session is stored with no refresh token (`refresh_token: None`), so it dies at the 4h Keycloak SSO cap and the deny branch then redirected the iframe document to the IdP. Follow-up (separate change): give the embed path a refresh token for silent renewal. |

---

---

### Batch 1aa — Embedded Streamlit token renewal ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.217 – 1.222 |
| Type | U |
| Files covered | `lex/authentication/views/token_views.py` (`StreamlitTokenView.post`, `_access_token_expiry`), `lex/proxy.py` (`_persist_jwt_to_session_if_needed`) |
| Test file | `lex/test_project/tests/init/test_1aa_embedded_token_renewal.py` |
| Test classes | `TestCluster01aa_EmbeddedTokenRenewal` |
| Fixtures | none (fake session dicts + an in-memory token store) |
| Est. tests | 6 |
| Coverage gain | the renewal path of the embedded Streamlit auth flow |
| Prereqs | batch 1z (the breakout response this reacts to) |
| Status | ✅ Complete — 6 pass / 0 fail |
| Note | the breakout batch made the expiry a graceful re-login; this removes the re-login. Two defects had to be fixed for renewal to be possible at all: the token endpoint never published an expiry (the only code returning one, `_generate_new_token`, is unreachable **and** self-signs HS256, which the RS256/JWKS proxy would reject), and `_persist_jwt_to_session_if_needed` returned early whenever the stored token was still valid — so a token renewed *before* expiry, which is the only time renewal can arrive, was discarded and the session died at the original deadline anyway. 1.220 is the gate on that second one: it fails against the pre-fix proxy. A refresh token was deliberately **not** given to the embedded path — it would have to travel through the iframe URL into access logs, history and `Referer` headers. |

---

### Batch 1ab — MCP tools infrastructure ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.223 – 1.256 |
| Type | U |
| Files covered | `lex/mcp_server/tools/embed.py`, `lex/tools/mcp_mode_invoke.py`, `lex/tools/setup_with_ai.py`, `lex/tools/verify_ai_assets.py` |
| Test file | `lex/test_project/tests/init/test_1ab_mcp_tools_infrastructure.py` |
| Test classes | `TestCluster01ab_EmbedPathClassification`, `TestCluster01ab_EmbedTitleBuilding`, `TestCluster01ab_FrontendUrlResolution`, `TestCluster01ab_CspOrigins`, `TestCluster01ab_NormalizeMcpMode`, `TestCluster01ab_NormalizeAiEnvironments`, `TestCluster01ab_UpdateEnvFile`, `TestCluster01ab_NormaliseMode`, `TestCluster01ab_InvokeSwitchResult`, `TestCluster01ab_InvokeSwitchFallback`, `TestCluster01ab_ResolveActiveMcpMode`, `TestCluster01ab_VerifyDirectory` |
| Fixtures | `tempfile.TemporaryDirectory`, `sys.modules` stubs for `lex.mcp_server.config` / `mcp` SDK (not installed on this branch) |
| Tests landed | **34 pass / 0 fail** |
| Coverage gain | embed URL routing, mode-switch invocation, env-file update, asset-directory verification |
| Prereqs | none |
| Status | ✅ Complete — regression coverage for PR #703 (`fix/mcp-server-fastmcp4-port`). |
