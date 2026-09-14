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

### Batch 1ae — Streamlit theme parity — tokens, native theme config, CLI wiring ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.274 – 1.299 |
| Type | U + I |
| Files covered | `lex/lex_app/streamlit/theme/{tokens,mapping,config_writer}.py`, `lex/bin/lex.py` (`_safe_theme_flags`), `lex/.streamlit/config.toml` (generated) |
| Phase 2 scope | Streamlit floor `>=1.58` only — the planned CSS layer was **dropped**; the native theme surface already covers the sidebar and dataframe header, and automatic CSS injection has no public hook (the `runpy` shim would break Streamlit's AST magic). Shipped theme therefore touches **no** Streamlit internals. See the design doc §7. |
| Test file | `lex/test_project/tests/init/test_1y_streamlit_theme.py` |
| Test classes | `TestCluster1y_Tokens`, `_Mapping`, `_StreamlitContract`, `_ConfigWriter`, `_Fonts`, `_LaunchFlags`, `_CommittedConfig` |
| Fixtures | none (pure data transforms; `tmp_path` for the file write) |
| Tests landed | **41 pass / 0 fail** |
| Coverage gain | Streamlit theme parity phase 1 — token source of truth, native theme mapping, CLI + file delivery, drift guard |
| Status | ✅ Complete — see the allocation note. Phase 1 of the design (`docs/superpowers/specs/2026-07-30-streamlit-theme-parity-design.md`); phases 2–4 (CSS layer, live host handshake, cross-repo tokens.json) are separate. |
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
| Scenario range | 1.280 – 1.285 |
| Type | U |
| Files covered | `lex/authentication/views/token_views.py` (`StreamlitTokenView.post`, `_access_token_expiry`), `lex/proxy.py` (`_persist_jwt_to_session_if_needed`) |
| Test file | `lex/test_project/tests/init/test_1aa_embedded_token_renewal.py` |
| Test classes | `TestCluster01aa_EmbeddedTokenRenewal` |
| Fixtures | none (fake session dicts + an in-memory token store) |
| Est. tests | 6 |
| Coverage gain | the renewal path of the embedded Streamlit auth flow |
| Prereqs | batch 1z (the breakout response this reacts to) |
| Status | ✅ Complete — 6 pass / 0 fail |
| Note | the breakout batch made the expiry a graceful re-login; this removes the re-login. Two defects had to be fixed for renewal to be possible at all: the token endpoint never published an expiry (the only code returning one, `_generate_new_token`, is unreachable **and** self-signs HS256, which the RS256/JWKS proxy would reject), and `_persist_jwt_to_session_if_needed` returned early whenever the stored token was still valid — so a token renewed *before* expiry, which is the only time renewal can arrive, was discarded and the session died at the original deadline anyway. 1.283 is the gate on that second one: it fails against the pre-fix proxy. A refresh token was deliberately **not** given to the embedded path — it would have to travel through the iframe URL into access logs, history and `Referer` headers. |

## Batch 1ac — Widget-host manifest construction and validation

- **Scenarios:** 1.251–1.258
- **Type:** U (pure; no Streamlit runtime, no DB, no browser)
- **Files covered:** `lex/lex_app/streamlit/widgets/spec.py`
- **Test file:** `lex/test_project/tests/init/test_1ac_widget_host_manifest.py`
- **Test class:** `TestCluster1ac_WidgetHostManifest`
- **Fixtures:** none
- **Status:** complete — **8 pass / 0 fail**

Companion to the `lex_widgets()` host (design:
`docs/superpowers/specs/2026-08-25-streamlit-widget-host-design.md`). The manifest is the
contract between Python and the embedded React host, and the failure it must not have is the
silent one — a widget absent from a dashboard because a key was misspelled, with a page that
renders cleanly and nothing logged. Validation therefore raises at the `page.calculation(...)`
call site so the traceback points at the author's line.

Frontend twin: PAC batch **12c** (`F12.23–F12.31`) validates the same manifest on the consuming
side, including the `?manifest=` base64url fallback that lets the route be opened in a browser
with no Streamlit at all.

## Batch 1ad — Streamlit theme follower

- **Scenarios:** 1.261-1.292
- **Status:** complete (11 pass)
- **Source under test:** `lex/streamlit_theme.py`, wired by `lex/streamlit_app.py`
  (`render_theme_follower`) and `lex/proxy.py` (`/_lex/theme-relay`)
- **Test file:** `lex/test_project/tests/init/test_1ad_streamlit_theme_follower.py`
- **Test classes:** `TestCluster1ad_StreamlitThemeFollower`, `TestCluster1ad_ThemeFollowerEncoding`

Closes the loop on cross-origin theme sync. The relay writes the agreed mode into
the Streamlit origin's `localStorage`; every Streamlit tab on that origin — embedded
or standalone — gets a `storage` event and reloads with a corrected
`embed_options`, because Streamlit reads the theme only at boot.

What the scenarios protect:

| Scenario | Property |
|---|---|
| 1.261-1.262 | Both `embed_options` spellings parse — repeated params (documented) and comma-joined (what people actually type) |
| 1.263 | "No theme requested" ≠ "light requested", so a tab the user opened never reloads on a guess |
| 1.264 | A contradictory URL resolves the same way every time rather than raising into a void |
| 1.265 | No unreplaced `__KEY__` reaches the browser, where it would fail silently |
| 1.266 | The storage key has exactly one Python definition |
| 1.267 | The script uses `parent`, never `top` — `top` is cross-origin when lex-app embeds Streamlit and throws on every access |
| 1.268 | The mode is encoded as data, including against `</script>` |

**Closed gap (was: known gap).** The script's own logic — read the agreement,
compare, decide — cannot be proved by any assertion about a string, so it was
covered by a DOM-double harness run by hand. That harness lived in a temp
directory and was gone by the next session, and the reload loop it had already
proved fixed came back in a different form. It now lives at
`lex/test_project/tests/init/harness/theme_follower_harness.mjs` and runs under
pytest (scenario 1.280) when `node` is on the machine, skipping when it is not —
a missing JS runtime is a fact about the machine, not a defect in the code.

It models the one distinction a reload loop turns on: state that survives
`location.reload()` (`localStorage`, `sessionStorage`) and state that does not
(the window, its listeners, every flag on it). Eight cases, 18 checks.

### Batch 1ad addendum — the in-frame path (scenario 1.269)

The relay alone did not fix a real deployment: a light Streamlit page went on
hosting dark widgets. Cause: `localStorage` in a **cross-site** iframe is
partitioned by top-level site in current browsers, so the relay framed by lex-app
writes to a partition the standalone Streamlit page never reads. The relay is
still correct for same-site deployments; it is not sufficient.

The widgets are *children* of the Streamlit page, so that boundary needs no
storage at all:

```
widget frame (lex-app origin)  --postMessage 'theme'-->  shim (Streamlit origin)
                                                            |
                                              writes lex.theme.mode HERE
                                                            v
                                        follower in the page reloads with
                                            corrected embed_options
```

The shim is served by the Streamlit server, so it is same-origin with the page —
which is exactly why its write reaches the follower when the relay's does not.
Handled entirely in the shim: no `setComponentValue`, so no Python rerun, and the
author's `on_status` branch never sees a theme envelope.

Scenario 1.269 pins the three links, each silent when broken:

| Link | Failure if dropped |
|---|---|
| `render_widget_host` accepts and forwards `theme_storage_key` | shim has no key, returns without writing |
| both host call sites supply it (page **and** log dialog) | the dialog opens unthemed |
| the shim reads it from args | a second hardcoded copy, free to drift |

Frontend twin: F12.44–F12.45 (the emit side).

### Batch 1ad addendum 2 — lifetime of the follower (scenario 1.270)

Found by re-reading the follower against Streamlit's rerun model rather than by a
failing test. The install flag was on the **page** (persists across reruns); the
`storage` listener was on the **component iframe** (destroyed and recreated per
rerun). That pairing works for exactly one render:

```
render 1:  flag unset  -> install, listener on iframe A
rerun:     iframe A destroyed, iframe B created
render 2:  flag SET    -> return early, iframe B adds no listener
           => nothing is listening, nothing is logged
```

Both now live on the page. The initial read moved to the page's storage too,
which also handles the common ordering where an embedded widget announces its
theme *before* this block renders.

Also added: one `console.info` per decision (`[lex-theme] asked for … ; showing …`)
in the follower, and one in the shim on write. Three cross-context hops with no UI
of their own otherwise make "never arrived" and "arrived and was already correct"
look identical from the outside.

Harness re-run after the move: 7/7, including the new pre-existing-stored-value
case that covers the widget-writes-first ordering.

### Batch 1ad addendum 3 — shorten the embedded path (scenario 1.271)

Two rounds of debugging had been spent guessing which link in a five-link chain
was silent. The chain itself was the problem:

```
before:  widget -> shim -> localStorage -> storage event -> follower -> reload
after:   widget -> shim -> follower -> reload
```

The shim is already inside the Streamlit page's frame tree and same-origin with
it, so it never needed storage to reach the page. The follower now publishes
`host.__lexThemeFollow` and the shim calls it directly.

The storage route stays — it is the only way in for a writer holding no handle to
the page:

| Writer | Route |
|---|---|
| widget-host shim (embedded) | direct call, storage as backup |
| relay iframe (standalone page) | storage |
| a sibling Streamlit tab | storage |
| shim reporting before the follower rendered | storage, read at install |

Harness after the change: 8/8, covering the direct call, the storage route, the
pre-render ordering, and a mixed burst reloading once.

### Batch 1ad addendum 4 — the measurement was lying (scenario 1.272)

The bug that made every earlier fix in this batch look ineffective.

```
'rgba(0, 0, 0, 0)'  ->  old measurement says "dark"
```

Four zeroes pass a `length < 3` guard and compute a Rec. 601 luma of 0 — pure
black. So any page whose measured element painted no background of its own
reported **dark**. A light page asked to become dark then hit
`if (!now || now === mode) return;` and stopped, while logging that the page was
already correct.

Every previous round of debugging was downstream of a measurement that was
confidently wrong, which is why shortening the delivery chain changed nothing.

| Fix | Why |
|---|---|
| Guard on **alpha**, not component count | `rgba(0,0,0,0)` has four components — one *more* than the old guard required |
| Several candidate elements, in order | Which element carries the theme background is Streamlit's business and has moved between versions |
| OS preference as last resort | With no theme in the URL, that is what Streamlit itself follows — a reasoned answer, not a guess |

Harness: 10/10, including the exact regression (transparent `.stApp` on a light
page must still reload) and the inverse (transparent everywhere with an OS
preference of dark, told dark, must stay put).

### Batch 1ad addendum 5 — make it observable (scenario 1.273)

Four rounds of this were debugged by inference. The mechanism spans three
browsing contexts, the reports arrive as screenshots, and console output was not
reaching the diagnosis. So the state is now renderable in the page:

```
LEX_THEME_DEBUG=1
```

```
lex-theme diagnostics (LEX_THEME_DEBUG)
page showing: light   measured bg rgb(255, 255, 255)
url embed_options: (none)
stored on this origin: dark
widget last reported: dark via direct, 3s ago
follow entry point: function
reload already used: no
```

Each line answers one of the questions that previously needed a guess — in that
example, the theme arrived, the page measured correctly, and the reload had not
fired, which localises the fault to `follow()` rather than to delivery.

**Spliced, not gated.** When off, the panel code is *absent* rather than
present-and-skipped: no production page carries it, and no later edit to a
runtime guard can leak a debug box into a dashboard. 1.273 also pins that the
follower is byte-identical in both variants — diagnostics observe the mechanism,
they never alter it.

## Batch 1af — The mode switch and the branded theme compose (2026-08-28)

- **Scenario:** 1.300
- **Status:** complete (2 pass)
- **Source under test:** `lex/lex_app/streamlit/theme/` (batch 1ae) +
  `lex/streamlit_theme.py` (batch 1ad)
- **Test file:** `lex/test_project/tests/init/test_1af_theme_switch_preserves_brand.py`

Found by merging the two theme branches, not by either one alone.

Batch **1ae** brands Streamlit at launch from LEX design tokens. Batch **1ad**
switches mode at runtime by reloading with `?embed_options=light_theme|dark_theme`.
Read separately, 1ad looks like it *destroys* 1ae — the URL option seems to select
Streamlit's built-in palette, which would discard the branding on every follow.

It doesn't. In the 1.58 bundle the URL option is a preference **signal**, and the
resolver prefers the custom variant of that mode:

```js
Light: ["Custom Theme Light", "Light"]   // custom first, built-in as fallback
Dark:  ["Custom Theme Dark",  "Dark"]
```

`Custom Theme Light`/`Custom Theme Dark` exist **only** when the config supplies
both `[theme.light]` and `[theme.dark]`. A config with only the flat `[theme]`
section produces one unnamed custom theme that this table cannot reach — so the
switch would fall through to the built-in and the brand would vanish.

**So 1ad is safe because 1ae populates both mode sections** — a dependency neither
batch states. The config is generated, so collapsing it to a single flat section
would read as a harmless simplification: the theme would still look right until
someone switched mode.

Second half pins that the two mode vocabularies stay distinct — `"dark"`
internally, `"dark_theme"` in the URL — because Streamlit drops an unrecognised
embed option without complaint.

### Batch 1af addendum — `lex_view(theme=)`, the third mechanism (scenario 1.301)

Taken **selectively** from `origin/feat/lex-brand-tokens-and-theme-handshake`,
because most of that branch is superseded:

| Part of that branch | Taken? | Why |
|---|---|---|
| `_lex_view_component` theme handshake (+32) | **yes** | genuinely new; not on `lex-app-v2` |
| `embed.py` `theme=` parameter (+13) | **yes** | same |
| `lex_view callbacks.md` theme section | **yes** | documents the above |
| `design_system/lex_tokens.py` (733 lines) | **no** | `lex-app-v2` has 787 lines via #686, with a CI freshness gate — merging would be a 54-line regression |
| `.streamlit/config.toml` (+13) | **no** | batch 1ae's generated config is authoritative and byte-asserted |
| `tools/ai_faq.py`, `setup_with_ai.py`, CI workflow | **no** | superseded by #686 |

Direction matters. `lex_view(theme=)` pushes the host's mode **down** into an
embedded lex-app iframe. The theme follower (1ad) has lex-app own the mode and
Streamlit follow — the exact inverse authority. They coexist because the
envelopes differ by source tag (`lex-app-host` downward, `lex-app` upward) and
neither side listens to the other, so there is no loop.

**Documented gap, asserted not implied:** the React app reads neither `?theme=`
nor the inbound `theme` message, so `lex_view(theme=)` is inert on the frontend
today — while reading as if it works: it validates its input, appears in the URL,
and has no effect. The third assertion in 1.301 records that; it should be
deleted and replaced with an effect test when the consumer lands.

## Batch 1ag — SPA assets are cacheable, and responses are compressed (2026-08-28)

- **Scenarios:** 1.302-1.303
- **Status:** complete (4 pass)
- **Source under test:** `lex/react/views.py` (`serve_react`), `lex/lex_app/settings.py` (MIDDLEWARE)
- **Test file:** `lex/test_project/tests/init/test_1ag_spa_asset_caching.py`

Reported as "the loading speed... taking way too long". Measured, not guessed.

The built SPA is **one chunk of 6281 KB** (1895 KB gzipped), and `serve_react`
stamped **every** file with `no-store, no-cache, must-revalidate, max-age=0`.
`no-store` is the strongest form — the browser may keep no copy at all — so
nothing was reused across loads and nothing shared between a page and its
iframes:

| Page | Re-downloaded per load |
|---|---|
| main app + 1 widget iframe | 12.3 MB |
| main app + 3 widget iframes | 24.5 MB |
| main app + 13 widget iframes | 85.9 MB |

Hashed assets are content-addressed, so they are safe to cache forever and
same-origin iframes share one HTTP cache. `assets/…-<hash>.<ext>` now gets
`public, max-age=31536000, immutable`.

**The other half is tested just as hard**, because inverting the split is worse
than the original bug: a pinned `index.html` names hashed bundles a deploy has
replaced — blank app, 404s, no way to publish a correction. The hash is *required*
rather than inferred from the directory for the same reason.

`GZipMiddleware` added for the cold-cache path (6.3 MB → 1.9 MB), pinned near the
top of `MIDDLEWARE` since it compresses on the way out.

**Not fixed here — the next real win.** The 6 MB chunk itself. `src/index.tsx`
imports `ag-grid` at the entry, and `/embed/widgets` is registered *inside*
`<Admin>` beside six `<Resource>` declarations — so a Calculate button loads AG
Grid Enterprise, ten `ra-*` enterprise packages, `moment` and a markdown editor.
Route-level `React.lazy` cannot help, because the entry and `<Admin>` load first.
It needs a separate Vite entry mounting `<AdminContext>` only (no `AdminUI`, no
resources). The embed route's own import graph is **78 modules**.

## Batch 1ah — Per-user metadata is briefly cacheable (2026-08-28)

- **Scenarios:** 1.304-1.305
- **Status:** complete (9 pass)
- **Source under test:** `lex/api/views/ModelStructureObtainView.py`
- **Test file:** `lex/test_project/tests/init/test_1ah_metadata_cache_headers.py`

From the network log after batch 1ag landed: **171 requests** for one Streamlit
page, with `api/model-structure` fetched **five to six times**.

Nothing was looping. Each embedded lex-app frame is its own JS realm with its own
query cache, so six widget blocks meant six independent runtimes each wanting the
tree once. And that endpoint is expensive to *produce* — it deepcopies the
structure, then instantiates every model class and evaluates its list permission,
per request.

The browser HTTP cache **is** shared across same-origin frames, so
`private, max-age=30` collapses N fetches into one — no client-side coordination,
no shared-state machinery.

| Header | Why |
|---|---|
| `private` | The tree is permission-pruned. A shared cache serving it onward would disclose which models another user can see — a confidentiality bug, not a performance one |
| `max-age=30` | A staleness budget for permission changes, not a guess |
| `Vary: …, Cookie` | Extended, never overwritten — DRF sets `Vary` for content negotiation, and clobbering it misbehaves only under a cache |

`LEX_METADATA_CACHE_SECONDS=0` disables it. 1.305 pins that `0` means `no-store`
rather than silently falling back to the default (which would read as disabled
while still caching), and that a blank or malformed value cannot take the
endpoint down — it is an env var, so it will eventually be both.

Also applied to `model-styling`, the same class of per-user metadata.

## Batch 1ai — Component frames load without being scrolled to (2026-08-28)

- **Scenarios:** 1.306-1.307
- **Status:** complete (4 pass)
- **Source under test:** `lex/lex_app/streamlit/eager_frames.py`, wired from `lex/streamlit_app.py`
- **Test file:** `lex/test_project/tests/init/test_1ai_eager_component_frames.py`

Reported as "the components trigger when I scroll to them". The cause is
Streamlit's, not ours — `ComponentInstance` in 1.58:

```js
styled('iframe')(({ componentReady }) => ({
  display: componentReady ? 'initial' : 'none',
}))
<iframe data-testid="stCustomComponentV1" height={frameHeight ?? 0} ... />
```

A component's frame is `display: none` until the code **inside** it calls
`setComponentReady()` — which it can only do once loaded. Browsers deprioritise
hidden frames and commonly defer off-screen ones outright, so nothing fetches
until scrolling changes visibility. Streamlit's skeleton placeholder is what was
on screen.

`loading="eager"` on our inner iframe never had a chance: that frame lives
*inside* the hidden one.

The fix holds the frame open across its load — `display: block; height: 0`,
rendered but occupying nothing — and releases it the moment Streamlit writes a
height, which only happens after ready.

| Property | Why |
|---|---|
| Targets `stCustomComponentV1` only | A blanket iframe selector would force layout on elements hidden on purpose |
| Inline, never `!important` | Inline already beats the emotion class; `!important` would pin the frame at zero height *after* it loaded |
| One `release()` for all three exits | Ready, timeout, and detached. A frame left holding our styles is an **invisible** widget — worse than a slow one |
| MutationObserver | Streamlit rebuilds the tree each rerun; a one-shot sweep would only ever catch the first render |

Behaviour verified against DOM doubles (7 cases). No JS runner in this repo, so
that harness is not in CI — the Python scenarios pin the structural properties.

### Batch 1ad addendum 6 — following is switchable (scenario 1.274)

Reported: *"the streamlit is always in dark mode, you cannot change it."*

That is a direct consequence of how following works. It reloads with
`?embed_options=<mode>_theme`, and in Streamlit's resolver the URL is checked
**first** — above the stored theme, and above Streamlit's own theme menu. So a
followed page has lost its theme control from the user's side: the menu stops
responding, and `_streamlit_structure` cannot override it either, because a query
parameter is not something app code gets a say in.

Ruled out first: the launch flags merged in 1ae are **not** the cause — they emit
`backgroundColor=#ffffff` with proper `theme.light`/`theme.dark` blocks and no
`base` override.

**Default is on**, by product decision: the two surfaces are meant to read as one,
so lex-app decides the mode and Streamlit matches — rather than branded widgets
sitting on a mismatched page. The cost above is the accepted trade, and it is
stated in the API docstring rather than left to be discovered.

```bash
LEX_THEME_FOLLOW=0   # opt out where a page needs its own theme control
```

The escape hatch is **tested**, not just documented — in every spelling an
operator would reach for, and a blank value reads as *on*, because an unfilled
deployment template must not silently disable a default.

A page already pinned is freed by opening it once **without** `embed_options` in
the URL — with following off, nothing puts it back.

The eager-frames script (batch 1ai) is deliberately **not** gated on this. It
governs *when* component frames load and has nothing to do with the theme.

### Batch 1ad addendum 7 — light on first load (scenario 1.275)

Reported as *"both are always dark at first"*. Neither product chose it —
Streamlit falls back to `prefers-color-scheme` by design, and react-admin resolves
`defaultTheme || (prefersDarkMode && darkTheme ? 'dark' : 'light')`, so merely
*supplying* a `darkTheme` handed lex-app's default to the OS.

The follower now treats "nothing agreed yet" as **light** rather than leaving the
page on Streamlit's own default. lex-app sets `defaultTheme="light"` on both
`<Admin>` and `<AdminContext>` (frontend F12.48), so the two agree on first paint.

Cost: at most one reload, and only where the page was about to be the wrong
colour — on a light machine the measured mode already matches and `follow()`
returns without acting.

Verified against DOM doubles that it **terminates**: the reloaded page
re-evaluates with the mode now in its URL and stops. No loop.

A stored choice still wins on both sides. This decides the first load only.

### Batch 1ad addendum 8 — cooperate with Streamlit's theme menu (scenario 1.276)

The user opened Streamlit's Settings dialog, saw **Choose app theme → "Use system
setting"**, and said *"I think this is overriding the streamlit behaviour."*

That was the root cause, and everything in this batch before it was downstream of
it. Following used `?embed_options=<mode>_theme`, which sits at the top of
Streamlit's resolver — so the menu not only stopped applying, it stopped
**saving**:

```js
Cae = e => { if (!Pa() || (Rw(), xg() || Sg())) return; /* persist */ }
//                              ^^^^^^^^^^^^^ a URL theme is present
```

The follower now writes the key the **menu itself** writes:

```
stActiveTheme-<pathname>-v2   ->   JSON "Light" | "Dark" | "System"
```

so the two cannot disagree. The menu keeps working, shows the truth, and a choice
made there persists. A reload is still required — Streamlit reads the theme at
boot — but the URL is left alone, which is the whole difference.

**1.272 was rewritten, not deleted.** The luma measurement it pinned is *gone*
rather than fixed: the mode is now read from Streamlit's selection instead of
inferred from pixels, and "System"/unset both mean the OS decides, which
`prefers-color-scheme` answers exactly. Its job now is to stop anyone
reintroducing measurement.

A URL that already pins a theme makes the follower **stand down** and log how to
clear it — earlier versions put those parameters there, and a pinned tab would
otherwise spend its one reload per load, forever.

Harness after the rewrite: 8/8.

### Batch 1ai addendum — the frames the first fix missed (scenario 1.308)

Reported after the first version: *"some iframes load when we scroll to them."*
**Partial** success is the shape of a race, not a wrong mechanism.

Streamlit flips a component frame between hidden and shown by swapping the
emotion **class** — an attribute change, not a DOM insertion. The observer
watched `childList` only:

```
frame exists, not yet display:none  ->  first sweep checks it, skips it
Streamlit swaps the class           ->  attribute change, no callback
                                    ->  never looked at again
```

Those were the ones still waiting for a scroll.

| Change | Why |
|---|---|
| `attributes: true` with `attributeFilter: ["class", "style"]` | The class swap is the signal. Filtered, because *every* attribute would fire on each `height` write Streamlit makes as components report in |
| Bounded backstop re-sweep | Covers orderings not yet thought of — after two rounds of exactly that, worth paying for. Bounded, because an unbounded timer on a dashboard left open all day is a worse and quieter bug than the one it fixes |

Harness after the fix: **6/6**, including the exact regression — a frame hidden
*after* the first sweep, driven through an attribute mutation with no insertion.

## Batch 1aj — Streamlit sidebar chrome (2026-09-01)

- **Scenarios:** 1.309-1.314
- **Status:** complete (11 pass)
- **Source under test:** `lex/lex_app/streamlit/sidebar.py`, wired from `lex/streamlit_app.py`
- **Test file:** `lex/test_project/tests/init/test_1aj_streamlit_sidebar_chrome.py`

The sidebar was a teal link floating in an empty navy column. Teal is this
palette's accent — it reads as *primary action*, and logging out is not one.

It now carries lex-app's chrome: brand lockup, signed-in user with an initials
avatar on the active-item tint, hairlines, and a log-out row weighted like a
navigation item. Identity sits in the **sidebar** rather than a top bar — the
reverse of lex-app, and deliberate: lex-app puts the user menu top-right because
its sidenav is full of navigation, while a Streamlit page has no top bar of ours
and a mostly empty sidebar.

**Two boundaries, both asserted, because both are invisible when crossed:**

| Boundary | Why it needs a test |
|---|---|
| Owns the **container** only | Two components deciding a page's navigation means the author's loses quietly, depending on call order |
| Depends on **no Streamlit internals** | No `data-testid`, no emotion class — an upgrade renaming one would break this by looking slightly wrong rather than by raising |

**Accepted cost of the second, named rather than discovered:** the log-out row is
not truly bottom-pinned, because pinning needs exactly those selectors. It is
last in call order instead, which puts it at the bottom without touching
Streamlit's layout.

1.309 covers the real hazard rather than the styling: the display name comes from
the identity provider and is rendered through `unsafe_allow_html`, so a hostile
`preferred_username` would execute in the session of whoever opened the
dashboard. Name, subtitle and the sign-out href are all escaped.

Colours derive from the vendored `lex_tokens` (`NAVY`, `TEAL`) rather than being
retyped, so the sidebar cannot drift from the product it imitates — silently, in
the one place a user sees both side by side.

### Batch 1aj addendum — the real logo (scenario 1.311)

The text wordmark is replaced by lex-app's own `dark-lex-logo.svg` — the same
file its sidenav imports, white wordmark on teal, which is what a navy surface
needs.

| Decision | Why |
|---|---|
| Vendored into `lex/assets/` | Already declared package data. Referencing the frontend build instead would break at the next deploy — those filenames carry content hashes |
| Placed by `st.logo`, not by us | Reported as misplaced when hand-placed: the sidebar's *user content* begins below Streamlit's header, so the image sat under the collapse control with the header's whitespace above it. `st.logo` renders into the header slot — top of the sidebar, on the collapse control's line, exactly where lex-app puts its own |
| Guarded on the file existing | `st.logo` raises on a missing path; a packaging mistake should cost the logo, not the page |

**Known limit, stated rather than discovered:** `st.logo` also renders in the
app's upper-left when the sidebar is **collapsed**, and that surface follows the
page theme — so this white wordmark is hard to see on a light page there.
Choosing a variant would mean guessing a client-side value server-side, which is
the trap this session already fell into once.

**It also surfaced an older packaging bug**, unrelated to the logo:
`lex.lex_app.streamlit._widget_host_component` was missing from
`[tool.setuptools.package-data]`, so the widget-host shim's `frontend/` would
**not ship in a wheel** — `lex_widgets()` would fail to find its component on any
non-editable install. An editable install reads the source tree, which hides it
completely.

Now declared, and 1.311 asserts the *rule* rather than the instance: every
`_*_component` that ships a `frontend/` must appear in package-data.

### Batch 1aj addendum 2 — ordering (scenarios 1.310/1.311)

Marked up on a screenshot: logo to the very top, identity and log out to the
bottom. Streamlit's sidebar is:

```
stSidebarContent -> [ stSidebarHeader   (logo, collapse button) ]
                    [ stSidebarNav      (st.navigation's list)  ]
                    [ stSidebarUserContent                      ]
```

So `st.logo` already lands above even the page navigation — the logo appearing
mid-sidebar was a **stale module**, not a placement bug: `_streamlit_structure.py`
is watched and reloads, while `sidebar.py` is an installed package held in
memory. Only identity actually had to move.

It is now one **account block** — identity and the way out belong together —
rendered after `main()`, so the app's navigation sits above it with neither side
coordinating.

**One selector, and its bargain.** Call order alone puts that block under the nav,
not at the foot of the panel, and pinning genuinely needs a selector. So there is
exactly one:

| | |
|---|---|
| It is a `data-testid` | Streamlit's own testing surface — far more stable than a generated emotion class |
| It sets layout only | Asserted: no colour, visibility, or `!important`. A rule that did would fail *invisibly* and unattributably |
| It degrades | If it stops matching, the block sits in normal flow — where it would be anyway. Failure mode is "not pinned", not "broken" |

### Batch 1aj addendum 3 — one logo per background (scenario 1.312)

The previous round *named* this limit; this fixes it. `st.logo` fills two slots
with different backgrounds, and both were getting the dark file:

| Slot | Background | Variant |
|---|---|---|
| Sidebar | brand-navy in either mode | `dark-lex-logo.svg` — white wordmark |
| App upper-left (sidebar **collapsed**) | follows the page theme | `lex-logo.svg` — navy wordmark, via `icon_image` |

Reported as the collapsed logo being nearly invisible, which is exactly what a
white wordmark on a light page looks like.

**Two files rather than one adaptive SVG, on purpose.** An SVG can switch fills
on `prefers-color-scheme` — but that follows the *operating system*, while both
products deliberately default to light regardless of it. A dark-OS user on a
light page would get white on white: the same class of mismatch this cluster
spent 1.261–1.276 removing.

The test asserts the **fills**, not the filenames. The names differ by one word,
the files by three hex values, and swapping them yields a logo invisible on
exactly one surface — which no reviewer would catch and a filename assertion
would happily pass.

**Residual, still stated:** collapsed *and* dark gives navy on dark. `st.logo`
takes one image per slot, and choosing between them would mean reading a
client-side theme from Python.

### Batch 1ad addendum 9 — the reload loop (scenario 1.277)

Reported as the page flipping between light and dark without stopping. The guard
was on the wrong object:

```js
if (host.__lexThemeReloading) return;
host.__lexThemeReloading = true;
host.location.reload();          // destroys the window holding that flag
```

That stopped a second reload *within* one load and nothing across them. Two
independent inputs feed `follow()` — the stored agreement, and a widget reporting
its own palette — so when they disagree, each load flips the other way.

They disagree precisely when the widget frames can't see lex-app's storage
(third-party frames get partitioned storage) and fall back to the light default
while the agreement key says dark.

**The ledger now lives in `sessionStorage`** — it survives a reload and is scoped
to the tab, which is the lifetime a cross-reload guard actually needs. It records
what was last reloaded *for*, so a contradiction is recognisable rather than
merely repeatable. Bounded to two reloads per episode, then it refuses and says
why.

**The guard must not become the bug in turn**, so `follow()` now knows how it
heard:

| Reason | Treatment |
|---|---|
| `storage` | A fresh, deliberate change made elsewhere — clears the ledger, always honoured |
| `install` / `widget` | Re-reads of existing state — the two that can argue |

And the window expires, so sync doesn't work once per tab and then quietly stop.

Harness reproduces the loop directly — agreement `dark`, widgets reporting
`light`, twelve loads — and asserts it terminates in at most two: **5/5**.

### Batch 1aj addendum 4 — sizing and alignment

Both slots marked up: bigger in the sidebar, and the collapsed one lined up with
the page text.

**Size** comes from `st.logo`'s own `size="large"` rather than CSS — a native API
beats a rule that has to survive Streamlit's markup.

**Alignment can't**, because each slot sits flush against a different edge:

| Slot | Was | Now |
|---|---|---|
| Sidebar | further left than the navigation it heads | inset to match the nav items |
| App header (sidebar collapsed) | against the window edge, while the page text began well inside it | inset to the content column |

Both are pure inset corrections, so they join the bottom-pin under the same
**layout-only** limit — which the scenario now asserts across *every* rule in the
block, not just the pin. That limit is what keeps the whole selector dependency
cheap to lose: if it stops matching, the logo is merely back where Streamlit put
it.

The content inset is one named constant, in `rem` — an inset in `px` wouldn't
track text scaling, and lining up with text is the entire point.


### Batch 1ad addendum — the reloads that were left (scenarios 1.278-1.280)

1.277 bounded the loop. Reported again anyway:

> Streamlit reloads after a moment, so I'll be using it and it reloads by itself.

Bounding stopped the *flipping*. It did not stop the *interruptions* — a bounded
episode still restarts every time the ledger's window expires, and the
contradiction that starts it never heals on its own.

**The three inputs to `follow()` are not equals.** That is the whole fix:

| Reason | What it is | Reload? |
|---|---|---|
| `install` | the page is booting anyway | yes — costs nothing |
| `storage` | somebody just chose a theme | yes — acting is the point |
| `widget` | an embedded frame describing *itself* | **no** |

Only the third arrives while someone is using the page, and it is the one that is
merely an observation. A widget saying "I am light" is not a request to reload the
dashboard. The report is still written to the agreed key, so the next natural load
picks it up.

It is also why the disagreement was *permanent*: widget frames are cross-site and
get partitioned storage, so they cannot read lex-app's real preference and report
its **default** — forever. Which is why the memory of a contradiction is now
sticky for the tab rather than windowed like the ledger. The ledger still expires,
so a deliberate change an hour later is not mistaken for the tail of an old loop;
"these two disagree" does not expire, because it stays true until something
changes it.

**Silencing the direct route looked complete and was not.** The shim writes the
agreed key *before* calling the page, and a same-origin iframe's write reaches its
parent as a `storage` event — the same shape as a person switching theme in
another tab. The identical report simply took the other road:

```
widget → shim → localStorage.setItem(...)  ──storage event──▶  follower  (reloads!)
              └─────────── __lexThemeFollow(...) ───────────▶  follower  (silenced)
```

The shim now marks the write as its own before making it, and the follower reads
route as route, not as authority. Conversely a genuine `storage` change now
outranks *every* refusal below it, including one this load already made —
otherwise the escape hatch the stand-down message advertises ("change the theme in
lex-app or Streamlit's menu") would be closed by the refusal that suggests it.

Case 7 of the harness is this exact path. It fails against the pre-fix script and
passes after, which is the only reason to have it.

**Then 1.278 shipped inverted**, and broke theme following outright — reported as
"theme switch isn't working", a light page hosting a dark widget.

Refusing a widget report its reload is true of a *re-assertion* and false of a
*report*. In a **same-site** deployment the widget frame is the only messenger:

```
lex-app writes preference → widget frame (same origin, CAN read it) → shim → page
```

The relay cannot cover for it, because the shim already wrote that value and
`storage` does not fire for an unchanged one. The change was swallowed whole.

The error was generalising "widget frames cannot see lex-app's storage" from the
partitioned **cross-site** case to every case. It is false when the origins match
— which is the deployment we actually run.

The rule is now **act versus forget**:

| Input | May reload? | May clear the loop memory? |
|---|---|---|
| `install` | yes | no |
| `storage` (unclaimed) | yes | **yes** |
| `widget` | yes | **no** |

A widget re-asserts on every rerun, so letting an assertion *forget* wiped the
record of a contradiction several times a minute and restarted the bounded loop
— that was the mid-use reloading. Denying the right to forget fixes it; denying
the right to act does not, and breaks the carrier. The sticky stand-down was what
actually stopped the reloads all along.

**Why it shipped green through a suite built to catch this:** no harness case had
a widget report carrying real *news* — every case re-asserted a disagreement.
Case 9 adds it, and fails against the broken commit.

### Batch 1aj addendum 5 — the bottom pin, for real (scenario 1.313)

The account block was asked to the foot of the sidebar twice. The first attempt
shipped CSS that matched and did nothing:

```css
div:has(> div[data-lex-account]) { margin-top: auto; }   /* live, and inert */
```

The child combinator bound to the innermost wrapper Streamlit puts around markdown
output. That element is not a flex child of the column, so `auto` had no free space
to consume. No error, no warning, and invisible to a test asserting the selector is
present — which is what the batch had.

So 1.313 asserts the two things the pin actually needs, both of which the broken
version failed:

- a **descendant** match, since Streamlit's wrapping depth is not ours to predict;
- an unbroken **flex column** from the panel down, or `margin-top: auto` resolves
  to zero however well the selector matches.

**This spends a Streamlit dependency**, reversing the batch's original "no
internals" boundary. Named rather than discovered: call order cannot put a block
at the foot of a panel, and the foot is what was asked for. The terms are the same
ones the rest of this cluster uses — `data-testid`, not a generated emotion class —
under the layout-only limit, so a Streamlit upgrade can only ever make it *not
pinned*.

**And no magic number.** `min-height: calc(100vh - 9rem)` reserved the header and
nav with a constant that is right for exactly one app: the nav's height is however
many pages the author declared. Smaller nav → the block floats mid-panel; larger →
a scrollbar in a sidebar with no reason to scroll. `min-height: 100%` on a flex
column asks for the same thing knowing nothing, and grows instead of clipping when
the content genuinely overflows.

**And the pin may not squash its neighbours** — read from Streamlit's own styles,
not assumed. `stSidebarContent` is `position: relative; height: 100%; overflow:
auto`: a *scroll* container, and not a flex container until this CSS makes it one.
Its header carries `height: theme.sizes.headerHeight` — a fixed height on a block,
a starting point on a flex item, where the default `flex-shrink: 1` would let the
column take it back and squash the logo on a short sidebar. The navigation below
is the author's and no more ours to compress. Both are held at `flex: 0 0 auto`;
the account block alone flexes, and only *grows* (`1 0 auto`). Letting it shrink
would squeeze the way out of the app on exactly the sidebar that is already too
full, while the real scroll container sits one level up and would have handled it.

**One test-authoring fix rides along.** Three times in this cluster, an assertion
forbidding a property tripped on a *comment* explaining why that property was
rejected — training the author to write worse comments to keep the suite green.
`_rules_only()` strips comments before asserting.


### Batch 1aj addendum 6 — what running it found (scenario 1.314)

Every earlier scenario in this batch asserts on markup as a *string*. Rendering
the component in a real Streamlit page — no lex-app, no auth, just `st.logo` +
`st.navigation` + `render_account` — found something no string assertion would:

`st.markdown(..., unsafe_allow_html=True)` parses **markdown first**, and
markdown has opinions about whitespace that HTML does not:

- a blank line ends an HTML block
- a four-space indent is a code block

The pretty-printed markup satisfied both. With no subtitle, the placeholder left
a whitespace-only line; markdown read it as blank, closed the HTML block, and
rendered the following indented `</div>` **as literal text in a code box** — in
the sidebar, to the user.

It was invisible in every screenshot taken until then because every account
anyone had looked at *had* an email to put in the subtitle.

Both builders now emit one line. Joined with a **space**, not welded: HTML
attributes are whitespace-separated, so joining with nothing turns
`target="_top"` and `style="..."` into a single unparseable attribute — the bug
the obvious fix introduces. The scenario asserts no builder emits a newline at
all, so no future edit can reintroduce a blank line or an indent.


### Batch 1ad addendum — what running it found (scenarios 1.281-1.282)

Reported a third time as "the theme is not working, the switch never works."
Every previous fix in this batch reasoned about the emitted string. This one ran
the real follower inside a real Streamlit page — `st.navigation`, the generated
custom theme, no lex-app and no auth, because neither is part of the mechanism.

That immediately established what was **not** wrong: the storage key is exactly
right (`stActiveTheme-/tree-v2`, matching what Streamlit writes from its own
menu), the value format is right (`"Dark"`, not `"Custom Theme Dark"`, even with
a custom theme installed), the follower is same-origin with the page, and
`__lexThemeFollow('light')` from a widget correctly wrote the key and reloaded.

Then two silent failures, either one sufficient alone:

**1.281 — a stale URL pin disabled the sync permanently.** A theme in the page's
URL outranks both this key and Streamlit's own menu. The previous answer was to
stand down and log how to clear it. But *earlier versions of this script put that
parameter there* — so standing down converted our own past mistake into a
permanent, per-URL failure, carried by any bookmark, pinned tab or shared link,
with a single `console.info` as the only evidence. It now strips the theme values
and keeps every other embed option; the page self-heals in one load.

**1.282 — the first correction waited for a frame that never came.** Install ran
inside `requestAnimationFrame`, which does not fire while a document is not being
rendered. Anyone whose Streamlit page finished loading **unfocused** never got the
correction at all — and that is the normal case here, since people open lex-app
and the dashboard side by side and look at one of them.

A/B against a real page at `visibilityState: "hidden"`:

| | theme key | page |
|---|---|---|
| before | `null` | OS default (dark) |
| after | `"Light"` | corrected (white) |

Nothing in that path needs a frame — it reads storage and a media query.

**Note for the next person.** Three assertions in this batch have now tripped on
the script's own *comments* rather than its code, and a check written for this
very fix did it again. Grep the emitted JS, and strip comments first.


### Batch 1ad — the mechanism replaced (scenarios 1.283-1.289)

> "It's slow and unreliable, I can easily break it."

Correct, and structural. Every version until now wrote Streamlit's stored theme
key and **reloaded**, on the premise that Streamlit resolves its theme once at
boot so a reload was unavoidable.

**The premise was false.** Streamlit's own menu changes the theme *live* — dark
to light with no navigation — because it goes through React state, not storage.

The reload was the whole problem. Slow: a change cost a full page load, two when
the page booted wrong. Unreliable: the ledger, the sticky stand-down, self-report
marking and the one-reload flag existed *only* to make reloading survivable.
None of them addressed the theme; each could get stuck. Deleting the reload does
not fix those bugs — it makes them unreachable. Scenarios 1.277-1.280 and the
DOM-double harness are retired with it.

The new flow is **one-directional**, which is what removes the arguing:

```
lex-app theme change
        │  clicks Streamlit's OWN theme control (live, no reload)
        ▼
Streamlit page theme changes
        │  RENDER event carries {base: "light"|"dark", ...}
        ▼
      shim ──postMessage──▶ widget frames
```

Nothing reports upward any more, so there is nothing to contradict.

**The shim had listened for RENDER since it was written, read `args`, and thrown
`theme` away.** Every part of the old design was reconstructing, badly, a value
Streamlit was already handing over for free on every render.

Verified end to end against a real Streamlit page with the real shim:

| path | result |
|---|---|
| Streamlit menu → page → widget | ~1s, no reload |
| `lex.theme.mode` → driver → page → widget | ~1s, no reload |
| `menuEverVisible` | `false` |
| `popoverLeftOpen` | `false` |

The menu-DOM dependency is the accepted cost, and it fails **harmlessly**: the
theme is left alone with a log, because nothing reloads or overrides. 1.285 pins
that the mask is removed on both exit paths — a mask left behind would hide the
real menu from the user permanently, which is worse than the flash it prevents.


### Batch 1ad — the actual root cause (scenario 1.292)

Four rewrites of the theme sync, each verified, each reported as still broken.
The mechanism was never the problem.

**The reporting environment runs Streamlit 1.54. `requirements.txt` has said
`streamlit>=1.58` since the theme work landed.**

The sync reads and drives Streamlit's *own* surfaces, and both changed shape:

| | 1.54 | 1.58 |
|---|---|---|
| stored theme key | `stActiveTheme-<pathname>` | `stActiveTheme-<pathname>-v2` |
| theme control in menu | *none* | `stMainMenuItem-theme-Light\|Dark` |

So the write-and-reload versions wrote a key 1.54 never reads, and the driver
looks for a control 1.54 does not have. Silent, total failure — while every
probe against a supported build passed.

**The lesson is procedural.** I read Streamlit's internals out of `.venv-test`
(1.58) and never once checked the version the app under test actually runs. A
version mismatch and a bug present identically from the outside; only the
environment distinguishes them. Verify the environment before verifying the code.

A floor nothing checks is not a floor, so `lex streamlit` now warns at launch,
naming the cost and the cure. A warning rather than a refusal — the rest of the
app is unaffected, and blocking a launch over a theme is the worse trade.
| Note | the breakout batch made the expiry a graceful re-login; this removes the re-login. Two defects had to be fixed for renewal to be possible at all: the token endpoint never published an expiry (the only code returning one, `_generate_new_token`, is unreachable **and** self-signs HS256, which the RS256/JWKS proxy would reject), and `_persist_jwt_to_session_if_needed` returned early whenever the stored token was still valid — so a token renewed *before* expiry, which is the only time renewal can arrive, was discarded and the session died at the original deadline anyway. 1.220 is the gate on that second one: it fails against the pre-fix proxy. A refresh token was deliberately **not** given to the embedded path — it would have to travel through the iframe URL into access logs, history and `Referer` headers. |

---

### Batch 1ab — Ignored client-role self-cleanup (`client-admin` platform role, LEX-5) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.223 – 1.229 |
| Type | U |
| Files covered | `lex/lex_app/management/commands/init.py` (`IGNORED_CLIENT_ROLES`, `strip_ignored_role_policies`, `delete_stale_ignored_role_policies`) |
| Test file | `lex/test_project/tests/init/test_1ab_ignored_role_policy_cleanup.py` |
| Test classes | `TestCluster01ab_IgnoredClientRolesSet` (1.223 ignore-set contents: `client-admin` in, `release-manager` gone), `TestCluster01ab_StripIgnoredRolePolicies` (1.224 stale `Policy - client-admin` removed from `auth_config`; 1.225 reference detached from a permission's `applyPolicies`, order preserved; 1.226 no-op when nothing stale), `TestCluster01ab_DeleteStaleIgnoredRolePolicies` (1.227 live delete by id via `get_client_authz_policies`/`delete_client_authz_policy`; 1.228 no-op when nothing live; 1.229 missing-id fail-fast, mirrors `delete_resources_individual`'s permission-id check) |
| Fixtures | `_make_sync_manager` (stubbed `kc_manager`, same pattern as cluster 1e/1g) |
| Tests landed | **7 pass / 0 fail** |
| Coverage gain | the LEX-5 admin-role-separation self-cleanup: `client-admin` replaces the abandoned `release-manager` in `IGNORED_CLIENT_ROLES`; a policy minted by an older lex-app for a now-ignored role is stripped from the in-memory `auth_config` (and detached from any permission's `applyPolicies`) before re-import, then deleted live from Keycloak once nothing references it |
| Status | ✅ Complete. Design: `local_wiki/projects/admin-role-separation-5/README.md`. The sync only ever ADDS `Policy - <role>` entries via Keycloak's `/authz/resource-server/import` endpoint (verified against the existing `ensure_client_role_policies`/`sync_standard_client_role_permissions` pattern — it never deletes what's absent from a re-imported payload), so the live delete of the stale policy is a separate step, run **after** `import_authorization_settings` succeeds so the detached `applyPolicies` reference has already landed in Keycloak and the policy-delete's referential-integrity check passes. Not independently verified against a live Keycloak instance (no cluster access from this change) — the live-delete ordering assumption is grounded in Keycloak's documented policy/permission referential-integrity behavior, not an in-repo test. |

### Batch 1ac — Streamlit session survival across an idle period ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.230 – 1.246 |
| Type | U |
| Files covered | `lex/proxy.py` (`internal_token`, `proxy` + `ws_proxy` credential precedence, `_jwt_user_payload`, `_session_user_payload`), `lex/streamlit_app.py` (`_adopt_header_token`, `_sync_tokens_from_headers`, `_pull_token_from_proxy`, `renew_access_token`, `authenticate_from_proxy_or_jwt`, `sync_keycloak_context_from_access_token`, `_token_refresher`, `start_token_refresh_thread_if_needed`, `_within_renewal_grace`), `lex/bin/lex.py` (`streamlit` — mints the shared secret) |
| Test file | `lex/test_project/tests/init/test_1ac_streamlit_session_survival.py` |
| Test classes | `TestCluster01ac_ProxyRenewalChannel` (1.230–1.235, 1.246), `TestCluster01ac_StreamlitTokenLifecycle` (1.236–1.245) |
| Fixtures | none — Starlette `TestClient` over the real `proxy.app`, a hand-signed SessionMiddleware cookie, capturing stand-ins for the HTTP and WebSocket upstreams, and a dict-backed `st.session_state` |
| Tests landed | **17 pass / 0 fail**; whole `init` cluster **315 pass / 13 skip, 87 subtests pass** |
| Coverage gain | the renewal path of a *running* dashboard, which had none: the pull endpoint and its secret guard, credential precedence on both the HTTP and WebSocket paths, and the Streamlit-side token lifecycle including the identity/expiry split |
| Prereqs | batches 1z (breakout) and 1aa (embedded renewal) — same auth path |
| Status | ✅ Complete — 14 of 17 scenarios fail against the pre-fix tree (the two pre-existing correct behaviours, 1.235 and 1.237, are kept as guards) |
| Note | Root cause was a lifetime mismatch nobody had named: `st.context.headers` is the handshake request, frozen for the life of a socket that Streamlit keeps open for hours, while the access token it carries lives minutes. Session auth then had local refresh *deliberately* disabled on the reasoning that the proxy manages it — true of the proxy's own traffic, but the proxy can only deliver a credential at handshake time, so nothing renewed the running script's copy. Three secondary defects compounded it: `auth_callback` sets `st_access` on every login and the cookie was consulted before the session, so a normal login reached Streamlit as `jwt` with `refresh_token: None`; `_sync_tokens_from_headers` adopted the header token on mere inequality, reverting each renewal on the next rerun; and the expiry path called `_invalidate_local_auth`, wiping identity and rendering "Missing user information" about headers that were present — sticky, because the next rerun re-read the same frozen headers. Renewal now pulls from the proxy, so the refresh token still has exactly one writer. |

### Batch 1ad — Streamlit's asset bundle served ungated, and session durability ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.247 – 1.302 |
| Type | U |
| Files covered | `lex/proxy.py` (`_streamlit_static_dir`, `_build_static_routes`, `_apply_asset_cache_headers`, `_assert_static_bundle_present`, `PUBLIC_PROXY_PATHS`, `public_proxy`, `_get_upstream_client`, `_upstream_send`, `_iter_upstream`, `_build_proxied_response`, `_ensure_jwks_ready`, `_fetch_jwks_blocking`, `_get_jwks`, `_safe_next_path`, `_login_path`, `_login_url`, `_current_relative_path`, `_query_without_auth_token`, `_is_document_request`, `login`, `auth_callback`, `proxy`, and the `SESSION_SECRET` / `SESSION_SAMESITE` / `_build_token_store` startup guards), `lex/bin/lex.py` (`streamlit` launch args, `_warn_if_sessions_are_not_durable`) |
| Test file | `lex/test_project/tests/init/test_1ad_proxy_assets_and_session_durability.py` |
| Test classes | `TestCluster01ad_PublicAssetBundle` (1.247–1.251), `TestCluster01ad_AuthBoundary` (1.252–1.255), `TestCluster01ad_UpstreamForwarding` (1.256–1.259), `TestCluster01ad_JwksResilience` (1.260–1.263), `TestCluster01ad_LoginReturnPath` (1.264–1.267), `TestCluster01ad_BootstrapTokenHygiene` (1.268–1.269), `TestCluster01ad_SessionDurabilityGuards` (1.270–1.274), `TestCluster01ad_LaunchConfiguration` (1.275–1.276), `TestCluster01ad_ForwardingHardening` (1.277–1.282), `TestCluster01ad_StartupHardening` (1.283–1.286), `TestCluster01ad_RenewalDelivery` (1.287–1.292), `TestCluster01ad_StaleConnectionRecovery` (1.293–1.295), `TestCluster01ad_AccessLogging` (1.296–1.299), `TestCluster01ad_RefresherSurvivesReruns` (1.300–1.302) |
| Fixtures | none — Starlette `TestClient` over the real `proxy.app`, a streaming stub (`_RawStream`) over the `_upstream_send` seam, and `_reimport_proxy_with` to exercise import-time guards |
| Tests landed | **56 pass / 0 fail**, 42 subtests |
| Coverage gain | the asset path, which had none: what is public, what stays gated, and what the proxy does to a response on the way back. Plus the JWKS availability path, the login return path, and every startup guard |
| Prereqs | batches 1z (breakout), 1aa (embedded renewal), 1ac (idle survival) — same auth path |
| Status | ✅ Complete — **27 of the first 30 fail against the pre-fix tree**. The three that pass (1.254 the authenticated boundary, 1.255 the WebSocket deny, 1.271 the permitted configurations still boot) are deliberate guards: they assert behaviour this change must *not* alter, and would be the first things to break if the public allowlist ever widened |
| Note | The three reports that prompted this — "session timeout resets my state", "starting Streamlit takes forever", "TypeErrors coming out of nowhere" — are one causal chain, and the arithmetic is the explanation. Streamlit 1.61 ships **365** JS chunks and names **107** in eager `modulepreload` tags, and the proxy authenticated all of them through a single catch-all whose deny happened before it looked at the path. So one credential-less moment is a hundred simultaneous 401s; a *lazily* imported chunk that 401s surfaces as `TypeError: Failed to fetch dynamically imported module` because Vite's dynamic `import()` has no other vocabulary for an HTTP error (hence `DownloadButton` and `DataFrame`, both lazy chunks, in the screenshots); and stripping `Content-Encoding` — which the old code had to do, having read the *decoded* body — inflated the eager set to 1.77 MB where 0.42 MB was enough. Two further defects had the same shape: `_get_jwks_sync` fetched inline with a **sync** client on the event loop *and* returned `None` on failure while holding valid keys, so one Keycloak blip after the hourly TTL lapse rejected every token in the cluster; and `SESSION_SECRET`/`TOKEN_STORE` both defaulted to per-process values, making a restart or a second replica indistinguishable from an expiry. Measured and explicitly **not** a cause: JWT validation at 0.045 ms/request, 16 ms across all 365 chunks. |

#### 1ad follow-up — scenarios 1.277–1.286

An adversarial review of the first pass (three independent finder angles over `lex/proxy.py`, `lex/bin/lex.py` and the React shell) found **ten further defects in the fix itself**, and these scenarios pin each one. Worth recording because the pattern repeats: every one lived in the *new* code, and four of them would have reproduced one of the three original symptoms by a different route.

| Scenario | Defect it pins |
| --- | --- |
| 1.277 | the `auth_token` strip redirect set only the session cookie, so it handed over **fewer** credentials than the response it replaced — fatal wherever the frame cannot use that cookie (Safari ITP, third-party blocking), which is exactly the deployment the change targets |
| 1.278 | a bodiless 304 yielded `b""`, which reaches GZipMiddleware as a body, skips its `minimum_size` guard, and gets a gzip header attached — uvicorn then raises "Response content longer than Content-Length". Real `aiter_raw()` yields zero chunks, so **only the test doubles took this branch** |
| 1.279 | `stream=True` moved body-read failures outside the `try`, turning a mid-body upstream death into a truncated `200` — a silent `SyntaxError` in a Vite chunk rather than a retryable status |
| 1.280 | `public_proxy` forwarded the client's `Content-Length` while sending an empty body, so a probe carrying a body answered 500 |
| 1.281 | the pooled client was handed across event loops; `is_closed` says nothing about which loop its sockets belong to |
| 1.282 | module-level `asyncio.Lock()`s bind on their first **contended** acquire, so they failed only under the concurrency the single-flight was written for |
| 1.283 | a non-numeric `LEX_PROXY_REPLICAS` raised at import — in the uvicorn worker thread, killing the proxy while Streamlit kept serving |
| 1.284 | the `/static` mount ignored `--server.baseUrlPath`, silently restoring the 401 storm with the bundle present so nothing warned |
| 1.285 | the CLI pre-flight mirrored two of five import-time rules, so the SameSite raises still died in the worker thread — the failure it exists to prevent |
| 1.286 | the pre-flight's `startswith("https://")` disagreed with proxy.py's case-normalising `httpx.URL(...).scheme`, so `HTTPS://host` passed the readable check and raised later |

Two more were fixed without a scenario: the lifespan JWKS warmup was awaited, and uvicorn runs lifespan startup **before** creating the listener, so it kept port 8501 closed for up to 10s while `lex streamlit` had already pointed the browser at it (now a background task); and `_reimport_proxy_with` restored `sys.modules["lex.proxy"]` but not the `lex.proxy` **package attribute**, leaving a later `from lex import proxy` bound to a throwaway module with a different `TOKEN_STORE`.

#### 1ad follow-up 2 — scenarios 1.287–1.290: the channel the other fixes needed

Freezing the iframe `src` is what stops a renewal destroying the Streamlit session. But it also removed the only way a renewed token could **reach** the proxy: `?auth_token=` in the iframe URL. So the two halves were mutually exclusive — deliver the token and lose the state, or keep the state and let the session die at the access token's own lifetime (~5 min, Keycloak's default), with the shell holding a fresh token it had nowhere to put.

Verified directly before fixing: bootstrap with a 3-second token, wait, and the next request is a **401** even though a valid renewal is sitting in React state. The only credential the proxy would accept was `?auth_token=`, i.e. an iframe reload.

`POST /auth/adopt` is the missing third option, and it reuses rather than replaces what was already there — `_persist_jwt_to_session_if_needed`'s strictly-newer adoption, whose comment already named the frontend as the intended source.

| Scenario | Asserts |
| --- | --- |
| 1.287 | a strictly newer token POSTed from the shell is adopted, and the session then outlives the token it was bootstrapped with |
| 1.288 | a foreign `Origin`, and no `Origin` at all, are both refused — the endpoint is credentialed so it can never answer `*`, and an unset `REACT_APP_URL` must close it rather than open it |
| 1.289 | the token is still JWKS-validated: junk is 401, absent is 400. This is what stops the endpoint being a way to install arbitrary identity |
| 1.290 | the CORS preflight succeeds for the allowed origin naming POST, and is refused for any other — without it the browser never sends the request, and the failure would be invisible except as a session that quietly stops renewing |

| 1.291 | `DOMAIN_HOSTED` alone permits adoption, with no extra variable set — the reason the allowlist is derived rather than declared |
| 1.292 | `DOMAIN_HOSTED=localhost` (the development default) yields the shell's real dev origins, not a trusted `https://localhost` |

The allowlist is **derived, not declared**, and that was the point: `settings.py` refuses to start without `DOMAIN_HOSTED` whenever `DEPLOYMENT_ENVIRONMENT` is set, and Django already builds `CORS_ORIGIN_WHITELIST` from it identically. A new *required* variable would have made the failure mode a dashboard that renews for five minutes and then quietly stops, with nothing in the logs — the class of bug this whole batch exists to remove.

The token travels in a request **body**, so unlike the bootstrap it never reaches the address bar, history, or a `Referer` — which also makes this strictly better than the mechanism `token_views.py` originally documented.

#### 1ad follow-up 3 — the guard that was worse than the bug

Scenario 1.270 originally asserted that the proxy **refuses to boot** when `SESSION_SECRET` is unset on an https deployment. That shipped, and immediately stopped a running instance from starting:

```
Error: SESSION_SECRET is not set, but STREAMLIT_URL/BASE_URL is https, so this
looks like a real deployment. ...
```

The reasoning behind the refusal was that degrading into the symptom is what made the original bug hard to find. That is true of a *broken* configuration and false of a *degraded* one, and this was the second kind: `lex streamlit` runs one process, so a per-process key works perfectly until the next restart, at which point users are logged out once. A dashboard that starts and loses sessions on redeploy beats one that does not start.

The durable fix was to stop needing a new variable rather than to demand one — the same shape as the `DOMAIN_HOSTED` correction in follow-up 2. `DJANGO_SECRET_KEY` is generated by terraform's `random_password`, kept in state, and shipped in the `app-env-*` Secret, so it is stable across restarts and identical on every replica: exactly the property session cookies need, already present on every instance. The key is *derived* from it via HMAC with a fixed label, so a leaked cookie-signing key does not hand over Django's secret or vice versa, and the published `settings.py` fallback is explicitly excluded — deriving from that would give every lex-app instance in the world the same signing key, which is worse than a random one rather than better.

1.270 now asserts the derivation, 1.271 asserts that **no** session-secret configuration refuses to start, and 1.276/1.286 assert the CLI pre-flight warns rather than blocks. What still refuses is the set that is genuinely broken: replicas without a shared token store, and a `SameSite=None` cookie without `Secure` that browsers discard outright.

#### 1ad follow-up 4 — scenarios 1.293–1.295: the regression pooling introduced

Found in a production log, not by review. One connection-pool detail undid part of the fix:

```
httpx.RemoteProtocolError: Server disconnected without sending a response.
  File "lex/proxy.py", line 1628, in proxy
  File "lex/proxy.py", line 1346, in _upstream_send
```

It fired **exactly 5 seconds** after the previous upstream request — uvicorn's default keep-alive timeout. Streamlit closes the socket at that point; the pool hands the dead connection to the next request, and it fails before a byte is exchanged. `RemoteProtocolError` is neither `ConnectError` nor `TimeoutException`, so it escaped both handlers as *"Exception in ASGI application"* — which reaches the browser as a dropped request rather than a status.

The per-request client this replaced could never hit it, because it never reused a connection. That is the cost of pooling, and it has to be paid explicitly:

| Scenario | Asserts |
| --- | --- |
| 1.293 | a request after the upstream's keep-alive expiry still succeeds, having opened a fresh connection — driven against a real socket server that answers and then closes, exactly as an expired keep-alive does |
| 1.294 | the pool's own expiry undercuts uvicorn's 5s, so the race is rare rather than merely survivable, and the configured value actually reaches the client |
| 1.295 | an upstream that never answers yields 502 on both the public and the authenticated path, instead of escaping |

**All three fail against the deployed `b7c1478a`.** The user-visible symptom this explains is *"Protokoll downloaden geht nicht"*: `/media/...` is proxied upstream, so a stale connection is a download that silently fails.

#### 1ad follow-up 5 — scenarios 1.296–1.299: the log that was missing

The production investigation in follow-up 4 stalled on a gap that is worth closing permanently: **the pod log contained no access-log lines at all.** A browser reporting `TypeError: Failed to fetch dynamically imported module` could not be traced to a status code, so "is the asset 200, 401 or 404?" — the one question that separates three completely different bugs — was unanswerable from the evidence available. It had to be answered afterwards by curling the live host.

Logged through the `lex` logger hierarchy rather than `uvicorn.access`, deliberately: two uvicorn servers run in this process (ours on 8501, Streamlit's own on 8080) and Django applies its own `dictConfig` after both, so whether `uvicorn.access` survives is not something this code can depend on. The `lex.*` loggers demonstrably emit in that same pod log, so that is what it uses.

| Scenario | Asserts |
| --- | --- |
| 1.296 | 4xx/5xx are logged at **WARNING**, with method, path and status — WARNING so they survive a deployment running at `LEX_LOG_LEVEL=WARNING`, which is the whole point |
| 1.297 | a *successful* static response is not logged unless `LEX_PROXY_ACCESS_LOG_STATIC=true`; 107 preloaded chunks per page would bury the one line anyone needs |
| 1.298 | the query string is never written out — the embedded dashboard is bootstrapped with `?auth_token=<jwt>`, and an access log that echoed it would move the credential into log storage and every downstream shipper |
| 1.299 | `LEX_PROXY_ACCESS_LOG=false` silences it without changing the response |

Also in this pass: the three remaining `print()` calls in `validate_jwt_token` now go through `logger`. They bypassed logging configuration entirely, which is why "JWT validation failed" never appeared alongside the records that would have explained it.

#### 1ad follow-up 6 — scenarios 1.300–1.302: the last live cause, found by the log we added

The access logging from follow-up 5 paid for itself on its first production log. That log showed the customer's whole session clean — no 4xx on any asset, upload 204, media 200, no `RemoteProtocolError` — and one thing left over:

```
Exception in thread token_refresher:
  File "lex/streamlit_app.py", line 298, in _refresher_should_stop
    return bool(st.session_state.get(stop_key, False)) or not _session_is_live(session_id)
streamlit.runtime.scriptrunner_utils.exceptions.StopException
```

The refresher carries a script run context so it can reach `st.session_state` at all. The cost is that a read goes through `SafeSessionState`, which raises `StopException` or `RerunException` to interrupt *the script* — and **both derive from `BaseException`, not `Exception`**, so the loop's `except Exception` could not catch them. The thread died with a traceback.

Why that is the original bug and not just noise: `start_token_refresh_thread_if_needed` replaces a dead thread, but only on the **next script run**. So if the interaction that killed it was the user's last, nothing restarts it, nothing renews the token, and the dashboard expires on the access token's own lifetime. It needs a rerun *followed by idleness* — which is exactly why it looked intermittent, and exactly the "left it open, came back, it was dead" report.

| Scenario | Asserts |
| --- | --- |
| 1.300 | `StopException` and `RerunException` share `ScriptControlException` and are **not** `Exception` subclasses — the fact that made `except Exception` look correct |
| 1.301 | a read interrupted by either is reported as "do not stop" and raises nothing |
| 1.302 | a genuine stop flag is still obeyed — the guard swallows control exceptions, not the answer, so "always return False" cannot pass |

Also fixed here: a **duplicated block in this test file** that shadowed scenarios 1.277–1.286. Python keeps the later class definition, so 63 methods were defined and only 53 collected — ten scenarios silently not running. The two copies differed only in 1.286's docstring; the kept one was the refined version, so deleting the shadowed copy loses nothing. `defined == collected` is now checked as part of running this batch.

## Batch 1ak — Widget keys and flow (record reconstructed)

| Property | Value |
| --- | --- |
| Scenario range | 1.316 – 1.326 |
| Test file | `lex/test_project/tests/init/test_1ak_widget_keys_and_flow.py` |
| Tests landed | **11 pass / 0 fail** (measured) |
| Status | ✅ Complete |
| Note | This batch landed with no row here and no entry in `allocation.yaml`: merge `3d14e959` truncated both shards. Range and count are measured from the file; the batch's own reasoning was never written down and is not invented here. |

## Batch 1al — The example dashboard runs when copied (record reconstructed)

| Property | Value |
| --- | --- |
| Scenario range | 1.327 – 1.330 |
| Test file | `lex/test_project/tests/init/test_1al_example_dashboard.py` |
| Tests landed | **4 pass / 0 fail** (measured) |
| Status | ✅ Complete |
| Note | As 1ak: reconstructed from the file after the shard truncation. |

## Batch 1am — Quackback launcher, exactly one per page (record reconstructed)

| Property | Value |
| --- | --- |
| Scenario range | 1.331 – 1.335 |
| Test file | `lex/test_project/tests/init/test_1am_quackback_launcher.py` |
| Tests landed | **6 pass / 0 fail** (measured) |
| Status | ✅ Complete |
| Note | As 1ak: reconstructed from the file after the shard truncation. |

### Batch 1an — HTTP/2-safe proxied response headers ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.336 – 1.341 |
| Type | U |
| Files covered | `lex/proxy.py` (`_SERVER_OWNED_RESPONSE_HEADERS`, `_REQUEST_DROP`, `_build_proxied_response`, the gzip scope in `_build_static_routes`) |
| Test file | `lex/test_project/tests/init/test_1an_proxy_http2_safe_headers.py` |
| Test classes | `TestCluster01an_Http2SafeResponseHeaders` (1.336–1.341) |
| Fixtures | none — a `_RawStream` stub shaped exactly like Streamlit's `/media` response, over the `_upstream_send` seam |
| Tests landed | **6 pass / 0 fail**, 2 subtests |
| Status | ✅ Complete — **4 of 6 fail against the unfixed tree**; 1.341 is the guard on the buffered branch and fails only when that branch's drop is removed |
| Note | Reported as: a download starts, sits at **0 bytes**, ends in a network error — while the proxy's own log shows nothing but `GET /media/....pdf -> 200`. Three defects combined, and **none is visible over HTTP/1.1**, which is why local development never saw it. (1) `Date` and `Server` were relayed from the upstream, so uvicorn's own copies made them duplicates — both are singleton fields (RFC 9110 5.5.2) and an HTTP/2 intermediary rejects a duplicated singleton by resetting the stream, which the browser reports as `ERR_HTTP2_PROTOCOL_ERROR` **against a 200**. (2) `Content-Length` was dropped from every proxied response, forcing `Transfer-Encoding: chunked` — forbidden outright in HTTP/2 (RFC 9113 8.2.2) — and leaving the browser no idea how big the download was, hence the literal "stays at 0 BYTE". (3) GZip wrapped the whole app, re-compressing already-compressed PDFs and ZIPs for no saving, on an event loop this process shares with the Streamlit script runner. The duplication **predated** the streaming change — verified against the pre-#750 proxy, which also emitted two of each — but the streaming change added the chunked framing and the re-compression that turned a tolerated violation into a fatal one. |
| Allocation note | First written as 1.300–1.305, which collided with `1af`/`1ag`/`1ah` on disk. The shard was reporting `max_scenario: 292` at the time because merge `3d14e959` had truncated the letters map — that truncation is also why `1ac`/`1ad` are each held by two batches. Renumbered to **1.336–1.341**, the first range free of everything on disk, and the letters map is restored in the same commit. The two double-allocated letters are recorded rather than renumbered (letters are never renumbered) and still need their owners to settle the overlap. |

**1.341 added on review.** `_build_proxied_response` treats its two branches differently — the streaming path *keeps* `Content-Length` deliberately, the buffered path must drop it — and 1.336–1.340 only exercised the streaming one (`_RawStream` exists to take that path). That left the branch where a stale header is **fatal** rather than merely wrong entirely uncovered, which was conspicuous next to the counter-guards already written for the other two asymmetries (1.339 for the gzip scope, 1.340 for the request side).

Both halves measured, and they fail for different reasons:

- a stale **length** — the compressed 40 against a decoded 5000 — makes a real uvicorn raise `RuntimeError: Response content longer than Content-Length`, and the client sees `http=200 bytes=0`. **That is the reported symptom verbatim**: a download that starts, sits at 0 bytes, and ends in a network error.
- a stale **encoding** raises `DecodingError: incorrect header check` in the client, which tries to gunzip bytes httpx already decoded.

Verified as a guard, not decoration: removing `drop | {"content-encoding", "content-length"}` from the consumed branch fails 1.341 and nothing else.
