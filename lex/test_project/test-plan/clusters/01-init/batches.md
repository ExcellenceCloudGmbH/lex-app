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

## Batch 1ao — a flow with an order

| | |
| --- | --- |
| Scenario range | 1.342 – 1.347 |
| Type | U |
| Files covered | `lex/lex_app/streamlit/embed.py` (`Flow` sequence builders, `Ref`/`ref`, `to_wire`, `__bool__`, `_check_resource`, the `lex_flow`/`lex_step` emission in `lex_view`) |
| Test file | `lex/test_project/tests/init/test_1ao_flow_composable_steps.py` |
| Test classes | `TestCluster1ao_TheSequence` (1.342), `TestCluster1ao_Ids` (1.343), `TestCluster1ao_Endings` (1.344), `TestCluster1ao_RefusedWhenWritten` (1.345), `TestCluster1ao_TheMappingIsUntouched` (1.346), `TestCluster1ao_TheCursorShips` (1.347) |
| Fixtures | none — `monkeypatch` over `render_lex_view_component` to capture the built URL, the seam 1.301 already uses |
| Tests landed | **29 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Asked for as: `Table1_create -> Table2_create -> Table3(id=3)_update -> Table4_create -> Table4(that id)_update -> table view`, with `loop`/`loop_last` as alternative endings and without hand-writing `"resource/operation"` keys. 1.342 is that chain verbatim. Design: [`docs/superpowers/specs/2026-09-18-flow-composable-steps-design.md`](../../../../../docs/superpowers/specs/2026-09-18-flow-composable-steps-design.md). |

**Two scenarios exist because the implementation got them wrong first**, and both are the kind of
wrong that produces no error anywhere.

`Flow` subclasses `dict`, so a flow built entirely from steps has an empty mapping and inherits
`__bool__` as `False`. `lex_view` guards the parameter with `if flow:` — which meant the first
working build serialised a correct program and then discarded it at that line. 1.342's third half
pins the override.

The second was caught by **1.325**, which already existed. `update()` had to mean two things — the
step builder and `dict.update` — and the dispatch routed the mapping branch straight to
`dict.update`, putting rules in without passing `__setitem__`. That is precisely the bypass 1.325
was written to prevent, three batches earlier and for the previous shape of this class. It failed
immediately.

**The frontend half is not in this cluster.** The cursor runtime lives in
`process-admin-general-client` (`useEmbedContext.ts`) and is covered by vitest there. What 1.347
pins is the contract between them: that a sequence ships `lex_step=0` and a mapping ships no cursor
at all — the distinction the frontend reads as "is a flow live".

### Batch 1ap — `lex reflex`: the command, its ports, and its workers' Django ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.348 – 1.356 |
| Type | U |
| Files covered | `lex/bin/lex.py` (`reflex` command, `REFLEX_CONFIG_TEMPLATE`, `_ensure_reflex_config`, `_reflex_hot_reload_paths`, `_resolve_reflex_port_flags`, `_SKIP_BOOTSTRAP_COMMANDS`), `lex/tools/django_bootstrap.py`, `lex/tools/project_root.py` (`load_project_env_file`), `generate_pycharm_configs.py` (`Reflex` run configuration), `.run/Reflex.run.xml`, `.vscode/launch.json` |
| Test file | `lex/test_project/tests/init/test_1ap_reflex_cli.py` |
| Test classes | `TestCluster01ap_TheCommand` (1.348–1.350), `TestCluster01ap_HotReload` (1.351, 1.351b), `TestCluster01ap_Ports` (1.352–1.353), `TestCluster01ap_RunConfiguration` (1.354), `TestCluster01ap_WorkerBootstrap` (1.355, 1.355b, 1.356) |
| Fixtures | a temp project directory as `PROJECT_ROOT_DIR`; Reflex's CLI entry point patched to record the arguments it would run with, invoked through click's `CliRunner` inside a forked `RegistrationContext` |
| Tests landed | **11 pass / 0 fail** |
| Status | ✅ Complete |
| Pinned sets extended | 1m's `EXPECTED_FILES` gains `Reflex.run.xml` and its explicit-command set gains `reflex`; 1y's configuration names gain `Reflex` — both are exhaustive lists by design, so a new command must be added to them |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.348 | `lex reflex` alone | runs `reflex run`, and the CLI does not set Django up for it |
| 1.349 | arguments pass through | every argument, `--help` included, reaches Reflex untouched; a command that acts on no project (`--help`, `login`) writes no rxconfig.py; only `run` is given ports |
| 1.350 | rxconfig.py | written once from the template that calls `lex_config()`, never overwritten, and Reflex runs from the project root |
| 1.351 | hot reload | `REFLEX_HOT_RELOAD_OVERRIDE_PATHS` names the project's own top-level entries — never hidden ones, `.web`, a virtualenv, or an empty value |
| 1.352 | development ports | each missing port is supplied (8502 frontend, 8503 backend); one chosen by flag, `REFLEX_*_PORT` or config wins |
| 1.353 | one port per mode | backend-only gets only the backend port, frontend-only only the frontend's, a prod/preview run only the single port it serves on — and none once the caller chose one |
| 1.354 | run configuration | `lex setup` scaffolds a "Reflex" configuration for PyCharm and VS Code, running `reflex run` |
| 1.355 | a worker's environment | `prepare_environment()` sets what the CLI sets (settings module, project root, `.env`) without overriding what is already set; `setup_django()` is a no-op once Django is ready |
| 1.356 | one `.env` parser | the CLI and the workers parse `.env` identically — comments, quotes, invalid lines, existing values |

Why the CLI does not bootstrap Django here: Reflex compiles and serves from worker processes that
import `rxconfig.py` and `lex.reflex_app` themselves, so the parent's Django setup would be wasted
and the workers' would be missing. The template therefore calls `prepare_environment()` before it
imports anything from `lex.lex_app`, whose settings module reads the environment once, at import.

### Batch 1aq — Reflex dashboards: Keycloak sign-in, the Django ORM, and the `?model=&pk=` dispatch ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.357 – 1.369 |
| Type | U + I |
| Files covered | `lex/lex_app/reflex/` (`config.py`, `auth.py` — `LexKeycloakAuthState`, `LexUser` —, `django_orm.py`, `dashboards.py`, `app.py`, `Reflex.py`, `examples/_reflex_structure.py`), `lex/reflex_app.py`, `lex/utilities/config/generic_app_config.py`, `lex/lex_app/apps.py` (`ready()`, `register_models`), `lex/process_admin/utils/model_structure_builder.py`, `lex/core/models/LexModel.py` (`reflex_main` / `reflex_class_main`) |
| Test file | `lex/test_project/tests/init/test_1aq_reflex_dashboards.py` |
| Test classes | `TestCluster01aq_Config` (1.357, 1.357b, 1.357c), `TestCluster01aq_KeycloakProvider` (1.358, 1.359, 1.369), `TestCluster01aq_Orm` (1.360–1.361), `TestCluster01aq_Dispatch` (1.362–1.363), `TestCluster01aq_PageChrome` (1.364), `TestCluster01aq_DispatchPage` (1.365, 1.365b), `TestCluster01aq_TheCompiledApp` (1.366), `TestCluster01aq_Registration` (1.367), `TestCluster01aq_EventLoops` (1.368) |
| Fixtures | probe models `ReflexProbeFund` (both hooks), `ReflexProbeDefault` (`LexModel`, no overrides), `ReflexProbePlain` (plain Django model); a session's state tree built from Reflex's root `State`; `E2ETestCase` for the ORM and dispatch scenarios; subprocesses for 1.357c and 1.368 |
| Tests landed | **22 pass / 0 fail** (21 pass / 1 skip where reflex is not installed) |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.357 | `lex_config()` | an `rxe.Config` importing `lex.reflex_app`, signing in through the Lex Keycloak provider without `offline_access` (an offline token would outlive the Keycloak session), Radix explicit, telemetry and the sitemap off, no port configured; overrides win and extra scopes add; the app name is a valid identifier derived from the project; importing it — what rxconfig.py does — creates no Reflex state |
| 1.358 | where the provider's settings come from | `LEX_KEYCLOAK_*`, then `OIDC_*`, then Lex App's own `KEYCLOAK_URL`/`KEYCLOAK_REALM`(`_NAME`) and `OIDC_RP_CLIENT_ID`/`SECRET`; never `KEYCLOAK_CLIENT_ID`; a clear error when nothing is set |
| 1.359 | the proxy's settings | `OIDC_ISSUER` widens the accepted issuers, `OIDC_VERIFY_SSL=false` switches TLS verification off, the button reads "Login with Keycloak" |
| 1.360 | `run_orm` / `@orm` | synchronous Django code that raises `SynchronousOnlyOperation` on the loop runs through them, returns its result, and raises its own errors |
| 1.361 | a request's connection handling, per event | stale connections are retired before and after every event (and on the loop's thread under `DJANGO_ALLOW_ASYNC_UNSAFE`), never inside an open transaction |
| 1.362 | the URL contract | `?model=&pk=` → record, `?model=` → table, neither → the project's own; an unknown model, a missing or malformed pk, and a model with no hooks each resolve to their own error view, checked in Streamlit's order |
| 1.363 | from the router | `LexDashboardState.resolve` and `current_record` read the URL an event sees and fetch through the ORM |
| 1.364 | embed and logout | only a truthy `lex_embed` hides the top bar; sign-out shows unless `is_logout_enabled` is explicitly falsy |
| 1.365 | the dispatch page | only models that override a hook are compiled in; a hook that is not a classmethod/staticmethod or returns no component fails at compile; `_reflex_structure.py` may be absent but never silently broken |
| 1.366 | the compiled app | sign-in and popup routes, no Reflex Enterprise pages, `/ui-error` (where a failed `@ui.load` sends the browser), the guard running before `resolve` on `/` and then the lex.ui opener as every lex page's load events end, the connection middleware, the standard scopes without `offline_access`, the structure's pages and the models' dashboards |
| 1.367 | registration | the report frames `REFLEX_URL` (default `:8502`) with `lex_embed=1`; it and its sidebar entry exist only with `IS_REFLEX_ENABLED=true`; discovery skips `_reflex_structure.py` and `rxconfig.py`, and the `reflex` directory only inside lex's own packages |
| 1.368 | granian's uvloop | with uvloop's loop current, a downstream project's Django setup carries on and patches nothing, and `async_to_sync` works after it. It used to assert that `_apply_nest_asyncio` skipped uvloop and patched asyncio's own loop; it was rewritten for BUG-046 in batch 1bo (below) |
| 1.369 | `LexUser` | `username` is `preferred_username`; `display_name` the name, else the username, else the email; both empty signed out |

**1.361 was strengthened after a mutation run.** Its first form asserted only that a connection
inside an atomic block survived an event, and a middleware that closed it anyway still passed:
Django's `close()` inside a transaction marks the connection (`closed_in_transaction`,
`needs_rollback`) instead of dropping it. It now pins both flags and a query in the same
transaction. Letting discovery walk the Reflex package fails 1.367. (Removing
`_apply_nest_asyncio`'s guard used to fail 1.368. The function is gone since batch 1bo.)

**1.357c caught a shadowed export.** The package serves its API lazily (PEP 562), and
`orm` was both the `@orm` decorator and the name of the submodule defining it. Importing
a submodule sets the package attribute of that name, so after the first import of
`lex.lex_app.reflex.orm` — which the app always makes — `from lex.lex_app.reflex import orm`
returned the module. The check that every name in `__all__` resolves to its own object
found it; the module is now `django_orm.py`, and 1.360 uses the public import path.

**Two scenarios run the way Reflex constrains them.** The auth plugin binds itself to the process
on the first compile and refuses a second, so 1.366 compiles the real `lex.reflex_app` once in
`setUpClass` and every assertion reads that compile. 1.368 runs in a subprocess because it checks
process-wide asyncio state, which a regression would otherwise leave patched for the rest of the suite.

The browser half — Keycloak's redirect and callback, the dashboards rendering signed in, embed
mode, sign-out, and a cross-site frame signing in through the popup — was verified end to end
outside the suite, against a mock OIDC provider, because the suite has no Keycloak or browser.

### Batch 1ar — Reflex dashboards sign in by themselves: no click, and the Keycloak session carries over — retired 2026-11-19

> **Retired 2026-11-19** with Reflex Enterprise (Reflex without Reflex Enterprise, PR B): its subject, Reflex Enterprise's `/login` page and silent sign-in, went with Reflex Enterprise; dashboards sign in through lex-app's hand-off ([1bx](#batch-1bx)). Test file removed; letter and scenarios kept.

| Property | Value |
| --- | --- |
| Scenario range | 1.370 – 1.375 |
| Type | U |
| Files covered | `lex/lex_app/reflex/auth.py` (`LexKeycloakAuthState.start_login`, `_silent_login_redirect`, `_handle_auth_callback_error_response`, `_redirect_to_logout_payload`, `_set_tokens`; `lex_login_page`; `_report_oversized_token_cookies`), `lex/lex_app/reflex/config.py` (`LOGIN_PAGE`, `lex_auth_plugin`) |
| Test file | `lex/test_project/tests/init/test_1ar_reflex_automatic_sign_in.py` |
| Test classes | `TestCluster01ar_TheLoginPage` (1.370), `TestCluster01ar_TopLevel` (1.371–1.372), `TestCluster01ar_Framed` (1.373–1.374), `TestCluster01ar_Tokens` (1.375) |
| Fixtures | a session's state tree built from Reflex's root `State`, its router on `/login` or `/callback`; Keycloak's authorization endpoint patched in (no discovery over the network); the running app stubbed where building and rendering `/login` asks for it (1.366 compiles the real one), in a temporary working directory, since rendering copies Reflex's shared assets into it; the plugin's out-of-band toast stubbed where a failure pushes one |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.370 | `/login` is lex-app's page | `lex_auth_plugin()` names `lex_login_page` by import path; the page renders "Signing you in…" and the "Login with Keycloak" button only under `login_needs_click`; on mount it runs `_LOGIN_PAGE_JS` and hands the answer to `start_login`; `login_page=` from the project wins; with any provider but lex-app's alone, exactly the plugin's palette |
| 1.371 | the top level | signed out, `start_login` returns the plugin's own redirect to the authorization endpoint — no `prompt`, the provider's client, callback and `state` — with `redirect_to` kept for after and the frame check recorded; signed in, a replacing redirect to `redirect_to`; the repeat of one visit returns nothing and leaves the running sign-in's nonce alone |
| 1.372 | never a loop | an attempt 5 s ago: the button, no redirect; one older than `AUTO_LOGIN_RETRY_SECONDS`: automatic; after `_redirect_to_logout_payload`: automatic at once; an authorization endpoint that raises: the button, with the plugin's error logged and its toast pushed |
| 1.373 | in a frame | the authorization endpoint with `prompt=none`, `response_type=code`, the provider's client and callback, its `state` nonce and the S256 challenge of its verifier; `redirect_to` kept; the frame recorded for the popup; after `_silent_login_failed`, the button without a second request; a request that cannot be built leaves nothing pending |
| 1.374 | "sign in first" | each of `login_required`, `interaction_required`, `consent_required` and `account_selection_required`, through `auth_callback`, redirects (replacing) to `login_url_for(<the page>)` with the button and no error message; `access_denied` after a silent request, and `login_required` without one, are reported by the plugin |
| 1.375 | storing tokens | `_set_tokens` clears `login_needs_click`, `_silent_login_failed` and `_silent_login_pending`; an access-token cookie over 4096 bytes logs one warning naming `_oidc_lex_keycloak_access_token_data_partitioned`, 4096 and "Full scope allowed"; the same cookie again, or tokens that fit, log nothing |

**Why `/login` signs in by itself.** A restart of the Reflex server kept asking a user who was
signed in to lex-app to click "Login with Keycloak". The plugin's palette waits for that click on
purpose — it does not bounce a visitor to a third party unasked — but lex-app has one provider, and
the visitor's Keycloak session answers without a form. The likeliest reason a restart lost the
session at all is a Keycloak access token larger than a cookie may be: the browser drops the cookie,
the new server finds only the ID token, and the page guard sends the visitor to `/login`. Signing in
by itself makes that a silent bounce through Keycloak, and 1.375's warning says what to change.

**Two guards were found in a browser, not by reasoning.** React's development mode mounts every
component twice, so `/login`'s check ran twice: the second call fell into the retry window and the
button flashed before every redirect — seen by watching the websocket, now pinned by 1.371's repeat.
And `start_login` first chained the plugin's `redirect_to_login`, which left it unable to see that the
redirect could not be built: the page would spin for ever. It calls it inline now, and 1.372 pins the
button that replaces the spinner.

**Thirteen mutations each fail a test here** — the repeat, the retry window and its reset on
sign-out, `prompt=none`, the silent-error branch, the button on a failed start, the tokens clearing
the click, the cookie warning's threshold and its once-only, the page's mount event, the signed-in
shortcut, the frame record, and the frame's fallback to the button.

The browser half was verified outside the suite, against the mock OIDC provider given a
Keycloak-like SSO session: an unsigned visit going straight to the provider's form; a visitor signed
in only to the provider signed in with no form; a restart, a dropped access-token cookie and a new tab
all signed in without a click; a frame signed in silently, and through one click when the frame could
not see the provider's session; the button, not a bounce, on coming back from the provider's form; and
the provider's form again, at once, after signing out.

### Batch 1as — Reflex dashboards: a Keycloak access token too large for its cookie is stored compressed — retired 2026-11-19

> **Retired 2026-11-19** with Reflex Enterprise (Reflex without Reflex Enterprise, PR B): the access-token cookie it compressed is gone: no token reaches the browser ([1bx](#batch-1bx)). Test file removed; letter and scenarios kept.

| Property | Value |
| --- | --- |
| Scenario range | 1.376 – 1.380 |
| Type | U |
| Files covered | `lex/lex_app/reflex/auth.py` (`LexKeycloakAuthState._set_tokens`, `_fit_access_token_cookie`, `_compress_token` / `_decompress_token`, `_read_access_token_cookie` installed as `AccessTokenMetadata.from_cookie_value`, `_report_oversized_token_cookies`) |
| Test file | `lex/test_project/tests/init/test_1as_reflex_token_cookie_compression.py` |
| Test classes | `TestCluster01as_TokenCookieCompression` (1.376–1.380) |
| Fixtures | JWTs shaped like Keycloak access tokens, with the user's roles in 3 to 160 clients under varied names (seeded); a session's state tree from Reflex's root `State`, its router carrying the `Cookie` header a browser sends; the ID token's signature check stubbed (it needs Keycloak's keys) where the plugin's own `_set_tokens` runs, the plugin's `_set_tokens` stubbed where only the value lex-app hands it matters |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.376 | stored compressed, read as issued | through the plugin's own `_set_tokens`, a 30-client token's cookie is stored marked and under 4096 bytes; `_access_token` returns the token as issued; `_expected_at_hash` is `compute_at_hash` of it; the tab hash is its SHA-256; `expires_at` survives |
| 1.377 | only where needed | a 3-client token is handed to the plugin unchanged; a compressed value — also one still too large — is handed on as it came and restores; a random token is left as it is; an opaque compressible token is compressed whole and restores |
| 1.378 | read defensively | six forged values read as `None`, and a session receiving one from the browser holds no access token; unmarked values read exactly as the unpatched plugin function reads them |
| 1.379 | what the log says | one INFO with the size as issued for two stores; a 160-client token warns once, "even compressed"; a random token warns once, without it; both name the cookie and "Full scope allowed" |
| 1.380 | the browser's cookie | a session built from a `Cookie` header alone reads the token as issued, and `has_any_token` holds |

**Why compression, and why this way.** A browser refuses a cookie over 4096 bytes, and a Keycloak
access token with full scope carries every role a user holds, client by client. The plugin keeps
the session in cookies and keeps tabs in step through them, so a refused cookie cost the session
on every restart and in every new tab, and one tab's sign-in signed the others out — reproduced in
a browser with a token Chromium itself refused. With Keycloak's configuration out of reach, the
token has to be stored differently; compressing it inside its own cookie is the least of the ways,
since everything else the plugin does stays its own. A provider cannot override the var that reads
the cookie — the plugin's providers are mixins, and Reflex copies a mixin's vars over a subclass's —
so the plugin's `AccessTokenMetadata.from_cookie_value`, its one reader, is patched to restore a
marked token; unmarked values pass through unchanged, and 1.380 fails if the plugin ever reads the
cookie another way.

**1.375 (batch 1ar) changed its fixture.** It exercised the warning with `"a" * 5000`, which never
reached compression because it is not in the cookie's `access_token=…` form; it now uses a random
token in that form, which compression cannot shrink.

**Mutations: ten of eleven fail a test here.** No compression, compressing a token that fits,
keeping a compression that grew, not installing the reader, no inflation cap, accepting a truncated
stream, the warning without "even compressed", logging every time, restoring without the JWT
header, and letting a damaged value raise. The eleventh — dropping the "already compressed" check —
is equivalent while the "only if smaller" guard stands: deflating base64 of deflated data never
shrinks it, so the second compression is always discarded.

Verified in a browser outside the suite, with the mock OIDC provider issuing an 8.9 KB
Keycloak-like token (2.6 KB compressed): top level, reload, a new tab, a restart, two tabs side by
side, a frame with the provider's session, and a frame on the dashboards' own site with no provider
session at all all stayed signed in; before, the new tab and the second tab were signed out.

### Batch 1at — a Reflex development run behind one port: Reflex Enterprise's single-port proxy gets the Starlette app — retired 2026-11-19

> **Retired 2026-11-19** with Reflex Enterprise (Reflex without Reflex Enterprise, PR B): it repaired Reflex Enterprise's single-port proxy; lex-app's own proxy is tested in 1by. Test file removed; letter and scenarios kept.

| Property | Value |
| --- | --- |
| Scenario range | 1.381 – 1.383 |
| Type | U |
| Files covered | `lex/lex_app/reflex/config.py` (`_repair_single_port_proxy`, called by `lex_auth_plugin`) |
| Test file | `lex/test_project/tests/init/test_1at_reflex_single_port_proxy.py` |
| Test classes | `TestCluster01at_SinglePortProxy` (1.381–1.383) |
| Fixtures | a project directory holding the rxconfig.py `lex reflex` writes, or a hand-written one that keeps lex-app's sign-in, loaded in a fresh interpreter; Reflex Enterprise's proxy module reloaded as it ships and restored afterwards; a stand-in for Vite on a free port, answering with the path it was asked for; `get_config` patched in Reflex Enterprise's app and proxy modules to a single-port `rxe.Config` |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.381 | repaired as Reflex loads rxconfig.py | in a fresh interpreter, the rxconfig.py `lex reflex` writes and a hand-written one calling `lex_auth_plugin()` both load as the project's configuration and leave the proxy taking `starlette_app` |
| 1.382 | one port, both servers | Reflex Enterprise's own registration under a single-port config adds one lifespan task; run by Reflex's lifespan runner, `/ping` is the backend's answer and `/funds/42?tab=history` the frontend server's, path and query string |
| 1.383 | once, and only where needed | a second build leaves the repair as it is; a proxy taking `starlette_app`, one taking both names, and the stand-in without asgiproxy are left unchanged |

**Why a repair, and why there.** A pod exposes one port, and a development run has two servers, so
the platform starts the dashboards in Reflex Enterprise's single-port mode: the backend answers its
own routes and proxies every other path to Vite. On reflex-enterprise 0.9.6 with Reflex 0.9.12 that
mode never mounted. Reflex binds a lifespan task's parameters by name — `app` gets the Reflex app,
`starlette_app` the Starlette app — and the proxy, written for the Starlette app, names its
parameter `app`, so it found nothing to mount on, logged "Unable to find the base Starlette app", and
every page 404'd behind the one port. Both are the newest releases, so no upgrade fixes it. Reflex
Enterprise imports the proxy when it builds the app, after rxconfig.py has run, so replacing the
module's function from `lex_auth_plugin()` — which every project's rxconfig.py calls, `lex_config()`
included — is enough. A proxy without an `app` parameter is left alone, so a fixed release ends the
repair by itself.

**Mutations: four of four fail a test here.** No call from `lex_auth_plugin()`, no `starlette_app`
guard (a proxy taking both names is wrapped), no guard at all (the repair wraps itself), and a
replacement keeping the name `app`.

Verified outside the suite with the platform's start script — development mode, the backend and Vite
on two ports, the public origin as `REFLEX_URL`: pages, Vite's assets and its socket, a backend-served
image, and the event socket's state updates in a browser, all through the backend's one port.

**Follow-up (2026-10-02): mounted on the Reflex backend.** In production the repaired proxy still
404'd every page. Handed the Starlette app, Reflex Enterprise mounts on the outermost one, and
Reflex reaches its backend there through a catch-all `Mount("")`; armira's `api_transformer` wraps
the backend in ASGI middleware (RFDS's security headers, its sign-out guards), so the descent stops
at the wrapper and the proxy lands behind the catch-all. The replacement now takes the Reflex app
and hands the proxy `app._api`, where Reflex itself mounts a compiled frontend. 1.382 runs the
backend bare and wrapped in middleware under a catch-all mount; the wrapped case fails on v2.3.3's
repair with the production 404. 1.383 drops the cases a name check decided: the replacement is
marked instead.

### Batch 1au — `lex --version`: the version /health reports, answered from anywhere ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.384 – 1.385 |
| Type | U |
| Files covered | `lex/bin/lex.py` (the `lex` group's `--version` option; the set of arguments `main()` answers without setting Django up) |
| Test file | `lex/test_project/tests/init/test_1au_lex_version.py` |
| Test classes | `TestCluster01au_LexVersion` (1.384–1.385) |
| Fixtures | a temporary directory named `release-smoke`; the child process gets the suite's environment without `PROJECT_ROOT`, `DJANGO_SETTINGS_MODULE` and `LEX_APP_PACKAGE_ROOT`, with the suite's checkout first on `PYTHONPATH` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.384 | the version /health reports | with `lex/_version.py` stamped after install, as a git-ref image is, `lex --version` through the console script's entry point prints `lex, version <stamped>` and exits 0 — not the package metadata's `0.0.0.dev0` |
| 1.385 | answered from anywhere | `python -m lex --version` in a directory that is no project, and that Django cannot load as an app, exits 0 with one `lex, version …` line |

**Why `lex._version` and not the package metadata.** `click.version_option(package_name="lex-app")`
reads the installed distribution's metadata, which is frozen at install time. An image built from a
git ref installs lex-app as `0.0.0.dev0` and then stamps `lex/_version.py` with the ref, and `/health`
reports that module — so the two would disagree on exactly the images where the version is hardest
to know any other way. The option takes `lex._version.__version__`.

**Mutations: two of two fail a test.** Reading the version from the package metadata fails 1.384
(`0.0.0.dev0` against the stamped label). Dropping `--version` from the set `main()` answers without
setting Django up fails both: Django setup crashes in `release-smoke`.

### Batch 1av — Startup hardening ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.386 – 1.392, 1.405 |
| Type | U (1.390–1.391 I) |
| Files covered | `lex/tools/project_root.py` (`find_project_root`), `lex/bin/lex.py` (module start), `lex/core/config.py` (`load_error`, `raw_values`, `validate_project_config`), `lex/lex_app/apps.py` (`_validate_project_configuration`, `async_ready`), `lex/helpers/cache_tables.py`, `lex/core/apps.py` (`post_migrate`), `lex/lex_app/settings.py` (`LEX_CORS_ALLOWED_ORIGINS`) |
| Test file | `lex/test_project/tests/init/test_1av_startup_hardening.py` |
| Test classes | `TestCluster01av_NestedProjectRoot`, `TestCluster01av_ConfigValidation`, `TestCluster01av_CacheTables`, `TestCluster01av_CorsOrigins` |
| Fixtures | temporary git monorepo with `backend/lex_config.py`; temporary `PROJECT_ROOT`s; the test database's cache tables dropped and recreated; fresh interpreters for the CLI and settings |
| Tests landed | **8 pass / 0 fail** |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-18-v2-security-hardening-internal-config.md) |

---

### Batch 1aw — a plain pytest run is set up like `lex pytest`: PyCharm's test tree works ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.393 – 1.404 |
| Type | U / I |
| Files covered | `lex/tools/pytest_plugin.py` (new); `lex/tools/django_bootstrap.py` (`prepare_environment(root)`); `lex/bin/lex.py` (`pytest_cmd`); `lex/tools/test_groups.py` (`collect_groups`); `generate_pycharm_configs.py` (test run configurations); `pyproject.toml` (the `pytest11` entry point) |
| Test file | `lex/test_project/tests/init/test_1aw_pytest_plugin.py` |
| Test classes | `TestCluster01aw_PlainRunIsSetUp` (1.393–1.396), `TestCluster01aw_MarkersAndFailures` (1.397–1.401), `TestCluster01aw_CopiedSetupCoexists` (1.402), `TestCluster01aw_LexPytestSharesThePlugin` (1.403), `TestCluster01aw_TestRunConfigurations` (1.404) |
| Fixtures | `pytest_plugin_targets/` — modules a child `python -m pytest` runs by explicit path, never collected by the suite; a temporary dist-info that registers the entry point; `DATABASE_DEPLOYMENT_TARGET=DOCKER-COMPOSE` with its variables removed, for a database that cannot be reached; a stand-in for a copied conftest, loaded through `PYTEST_PLUGINS` |
| Tests landed | **18 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.393 | the PyCharm way | `python -m pytest` on a model-importing, database-writing test passes with none of `lex pytest`'s environment; `prepare_environment(root)` loads that root's `.env`; a `UserWarning` stays unreported under the default warning policy and is reported with `LEX_SUPPRESS_WARNINGS=False` |
| 1.394 | inactive elsewhere | in a directory without `lex_test_config.yaml`, a test sees no settings module and unconfigured Django |
| 1.395 | the opt-out | `-p no:lex` brings back "settings are not configured" |
| 1.396 | the entry point | pyproject registers `lex = "lex.tools.pytest_plugin"`, which defines the three hooks |
| 1.397 | collect-only needs no database | against an unreachable database `--collect-only` lists the test and exits 0 |
| 1.398 | a database that cannot be set up | the same run for real exits 4 with "Could not set up the test database" and runs no test |
| 1.399 | groups are markers | a configured group raises no unknown-marker warning; an undeclared marker exits 4 |
| 1.400 | invalid test config | a payload without `tests_entrypoint` exits 4 with the loader's message |
| 1.401 | pytest-django alongside | the plugin refuses with a message naming `-p no:django`, registering nothing |
| 1.402 | a copied conftest | it stands down when the plugin went first; the plugin stands down when the environment was set up already |
| 1.403 | one implementation | `lex pytest` passes the module with `-p no:lex` and sets nothing up itself; `lex pytest-groups` passes it too |
| 1.404 | test run configurations | `Tests`, `Tests: <group>` as PyCharm pytest configurations and VS Code pytest launch entries; none without a test config |

**The warning policy.** The settings install lex's warning policy (`LEX_SUPPRESS_WARNINGS`,
on by default) when imported. pytest runs `pytest_load_initial_conftests` inside
`warnings.catch_warnings()`, so the filters the settings installed there were dropped when
the hook returned, and the plugin's runs reported warnings `lex pytest` never had (20 more on
`init` + `crud_api`). The plugin records the filters `django.setup()` adds and reinstalls them
at the start of `pytest_configure`, which pytest does not wrap; the two runs report the same
warnings again (3 and 3 on `init` + `crud_api`).

**Why `PYTEST_PLUGINS` for the copied conftest.** pytest registers `-p` plugins before
entry-point plugins and `PYTEST_PLUGINS` after them, as it registers conftests. Hooks are
called last-registered first, so a `-p` stand-in let the ordering pass with `tryfirst`
removed; through `PYTEST_PLUGINS` removing it fails 1.402.

**Mutations: every scenario fails one.** Dropping `django.setup()` fails 1.393; activating in
every directory fails 1.394; no collect-only skip fails 1.397; re-raising the database error
fails 1.398; not registering the groups fails both halves of 1.399; ignoring an invalid config
fails 1.400; no pytest-django check fails 1.401; removing `tryfirst` and removing the
`_TestState.saved_data` guard each fail one half of 1.402; not reinstalling the warning filters
fails 1.393's third half; the old `pytest_cmd` / `collect_groups` fail both halves of 1.403;
dropping `-m <group>`, leaving VS Code without the test entries, or writing them without a test
config each fail 1.404.

---

### Batch 1ax — the Process model feature removed from the public surface ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.406 – 1.409 |
| Type | I |
| Files covered | `lex/core/models/Process.py` (deleted), `lex/core/models/__init__.py` (`__all__`), `lex/process_admin/sites/process_admin_site.py` (process registry), `lex/process_admin/utils/model_registration.py` (Process branch, `_register_process_model`, `_RESERVED_CLASS_NAMES`), `lex/process_admin/models/utils.py` (structure node types) |
| Test file | `lex/test_project/tests/init/test_1ax_process_feature_removed.py` |
| Test classes | `TestCluster01ax_ProcessFeatureRemoved` (1.406–1.409) |
| Fixtures | a customer-shaped `Process(LexModel)` declared in the test module (`app_label="lex_app"`) — the name the framework gave back; `StructureProbeModel(LexModel)` and `StructureProbeReport(HTMLReport)` fed to a real `ModelCollection` with `auto_include_missing_models=False` |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.406 | not importable | `lex.core.models.Process` raises `ModuleNotFoundError`, `Process` left `__all__`, the five kept names are still advertised |
| 1.407 | no registry left | `processes`, `registerProcess`, `get_process_structure_func` and `_register_process_model` are all absent; `html_reports`, `registerHTMLReport`, `get_html_report_func` survive |
| 1.408 | the name is the project's again | `_validate_model_definition` accepts `class Process(LexModel)`; `LexModel` / `CalculationModel` / `HTMLReport` and the `Historical` prefix still raise `ValueError` |
| 1.409 | no `Process` node type | the `api/model-structure` payload types the model `Model`, the report `HTMLReport`, the branch `Folder`; no node anywhere carries `Process` |

**Why a regression matters:** a half-removed feature is worse than either state — a `registerProcess` that still collects classes looks like a working feature while `api/process/<name>` 404s, and a stale `Process` entry in the reserved-name set keeps rejecting a project's own model for a framework class that no longer exists.

---

### Batch 1bo — a project's startup leaves asyncio unpatched and `async_to_sync` usable (BUG-046) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.665 – 1.666 (and 1.368 rewritten) |
| Type | U |
| Files covered | `lex/lex_app/apps.py` (`LexAppConfig.ready()`; `_apply_nest_asyncio` removed), `lex/bin/lex.py` (the environment the child starts from; the comment on the `lex streamlit` uvloop guard), `lex/lex_app/asgi.py` (imported inside a running loop for 1.666) |
| Test file | `lex/test_project/tests/init/test_1bo_bootstrap_leaves_asyncio_unpatched.py`; 1.368 in `test_1aq_reflex_dashboards.py` |
| Test classes | `TestCluster01bo_BootstrapLeavesAsyncioUnpatched` (1.665, 1.666); `TestCluster01aq_EventLoops` (1.368) |
| Fixtures | a minimal downstream project per scenario (a temporary folder named `cluster1bo_*` holding `lex_config.py`), set up in a fresh interpreter started from this checkout (`PYTHONPATH`), with a `faulthandler` watchdog around the two `async_to_sync` calls; no database |
| Tests landed | **2 pass / 0 fail** (1.368 passes too). On v2.4.2 the watchdog fires in all three, because the second `async_to_sync` call blocks |
| Status | ✅ Complete. BUG-046 resolved. |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.665 | set up with no running loop | after a downstream project's `django.setup()`, `asyncio.run` is the standard library's, and two `async_to_sync` calls with thread-sensitive work both return, each running its work on the calling thread |
| 1.666 | set up inside the server's loop | the same when `lex_app.asgi` (what `lex start` serves) is imported inside a running asyncio loop, as uvicorn does |
| 1.368 (rewritten) | granian's uvloop | with uvloop's loop current, setup carries on, `asyncio.run` is the standard library's, and both calls return. The child now gets `PYTHONPATH`. Without it, a checkout that is not pip-installed cannot import lex in the child, which is why the old 1.368 was a v2.4.2 baseline failure |

**Why nothing in lex needs nest_asyncio:** it was added when `ready()` called
`asyncio.run(self.async_ready(...))` directly, inside the loop uvicorn sets Django up in.
`async_ready` has since moved to a thread of its own, and the startup reset now runs on one too
(8d). Streamlit, Reflex, Celery and Channels never use it. Downstream code that relied on its side
effects, such as a nested `run_until_complete` or `get_event_loop()` creating loops in worker
threads, now has to apply it itself.
### Batch 1bc — deployed secret key, session lifetime and PKCE ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.481 – 1.489 |
| Type | U (1.481–1.486, 1.489), I (1.487–1.488) |
| Files covered | `lex/lex_app/settings.py` (`PUBLISHED_DEFAULT_SECRET_KEY` + deployed check, `SESSION_COOKIE_AGE`, `OIDC_MIDDLEWARE_SESSION_TIMEOUT_SECONDS`, `OIDC_RP_USE_PKCE`, `OIDC_RP_FORCE_SECRET_WITH_PKCE`), `lex/proxy.py` (OIDC client registration: `code_challenge_method`) |
| Test file | `lex/test_project/tests/init/test_1bc_auth_settings_hardening.py` |
| Test classes | `TestCluster01bc_DeployedSecretKey` (1.481–1.483), `TestCluster01bc_SessionLifetime` (1.484–1.486), `TestCluster01bc_DjangoSignInUsesPkce` (1.487–1.488), `TestCluster01bc_ProxySignInUsesPkce` (1.489) |
| Fixtures | fresh interpreters importing the settings; an RSA key pair per module; the token endpoint mocked at `oauth2_authcodeflow.auth.request_post`; authlib server metadata patched on the proxy's client |
| Tests landed | **9 pass / 0 fail** against django-oauth2-codeflow 2.1.0 |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-auth-hotfixes.md) |

**Allocation note:** Letter bc, not ay/az/ba/bb: those are held by in-flight branches (ay on fix/streamlit-report-frames-unauthenticated and others, az/ba on release/2.1.x, ba/bb on feat/fleet-metrics-calculation-events), which also claim scenarios up to 1.480. The same letter and scenario IDs are used on lex-app-v2 and release/2.1.x, so a merge between the lines is a no-op.

---

### Batch 1bd — lex-app 2.5 on django-oauth2-codeflow 3.0: settings, routes and bootstrap ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.490 – 1.500 |
| Type | U (1.490–1.493, 1.496–1.497, 1.499–1.500), I (1.494–1.495, 1.498) |
| Files covered | `lex/lex_app/settings.py` (`MIDDLEWARE`, `KEYCLOAK_INTERNAL_URL`, `OIDC_OP_DISCOVERY_DOCUMENT_URL`, `OIDC_OP_ISSUER`, `OIDC_RP_CLIENT_AUTH_METHOD`, `KEYCLOAK_VERIFY_SSL`, `OIDC_TOKEN_ENCRYPTION_KEYS`, `CORS_*`, `OIDC_ALLOWED_REDIRECT_HOSTS`, `OIDC_LOGIN_REQUIRED_EXEMPT_URL_PATTERNS`, `OIDC_SESSION_EXEMPT_URL_PATTERNS`, `LOGGING`), `lex/core/security_flags.py` (`CORS`), `lex/core/instance_hosts.py` (new), `lex/lex_app/urls.py`, `lex/process_admin/sites/process_admin_site.py`, `lex/lex_app/management/commands/init.py` (`SNAPSHOT_CLIENT_KEYS`, `_snapshot_client_settings`, `_restore_client_settings`) |
| Test file | `lex/test_project/tests/init/test_1bd_auth_core_settings.py` |
| Test classes | `TestCluster01bd_MiddlewareOrder` (1.490), `TestCluster01bd_KeycloakSettings` (1.491–1.492), `TestCluster01bd_StrictCors` (1.493), `TestCluster01bd_CorsOnTheWire` (1.494), `TestCluster01bd_RedirectTargets` (1.495), `TestCluster01bd_InstanceHosts` (1.496), `TestCluster01bd_EventLog` (1.497), `TestCluster01bd_ContractRoutes` (1.498), `TestCluster01bd_InitKeepsClientHardening` (1.499), `TestCluster01bd_NoLibraryMigrationDrift` (1.500) |
| Fixtures | fresh interpreters importing the settings with a controlled environment (deployed / local, Keycloak, `LEX_INSTANCE_HOSTS`); django-oauth2-codeflow's `FakeOP` for the sign-in redirect; a mocked Keycloak admin client for `lex init`; Django's migration autodetector in a fresh, `lex`-bootstrapped process |
| Tests landed | **11 pass / 0 fail** against django-oauth2-codeflow 3.0.0 |
| Also changed | 1bc 1.484–1.488 (3.0 has no library clock: no cap and no 2.x timeout setting; the PKCE verifier is read from the pending sign-in keyed by `state`; the code exchange is checked against `FakeOP`'s token endpoint; 1.486 uses the library's W003 check), 1aa 1.217–1.219 (the token comes from the sign-in via `create_oidc_session`, not from session keys; the issuer side moved into `TestCluster01aa_EmbeddedTokenIssuer`), 1av 1.392 (strict CORS by default; `LEX_CORS_ALLOWED_ORIGINS` adds origins). Intent unchanged in each. |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-auth-long-sessions-core.md) |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.490 | middleware order | Session → CORS → Common → CSRF → Auth → Bearer → OIDCSession → … → API-key login → permissions; no 2.x aliases; library E001 passes |
| 1.491 | internal discovery, public issuer | discovery on `KEYCLOAK_INTERNAL_URL`, `OIDC_OP_ISSUER` on `KEYCLOAK_URL`; PKCE + `client_secret_post`; no 2.x names |
| 1.492 | TLS and encryption keys | `KEYCLOAK_VERIFY_SSL` true when deployed, false locally, env wins (bool or CA path); `OIDC_TOKEN_ENCRYPTION_KEYS` list or None |
| 1.493 | strict CORS | exactly the instance origins + `LEX_CORS_ALLOWED_ORIGINS`, no localhost when deployed, no `*`, no allow-all; `LEX_SEC_CORS=false` allows all |
| 1.494 | CORS on the wire | an allowed origin is echoed with credentials; a foreign origin gets no `Access-Control-Allow-Origin` |
| 1.495 | redirect targets | `next` = a `LEX_INSTANCE_HOSTS` host or a path reaches Keycloak; a foreign site is refused with 400 |
| 1.496 | `LEX_INSTANCE_HOSTS` | JSON shape → hosts/origins; fallback to `DOMAIN_HOSTED`/`STREAMLIT_URL`/`REFLEX_URL`; bad input dropped without raising |
| 1.497 | sign-out events visible | `event=oidc.session.ended` reaches stdout at INFO with `LOG_LEVEL=WARNING`, deployed and local |
| 1.498 | contract routes | `api/auth/session`, `oidc/auth-complete` resolve to lex's views before `oidc/`, also in the process-admin site; login-exempt; look-alikes are not |
| 1.499 | `lex init` keeps hardening | snapshot holds `attributes` and `frontchannelLogout`; restore merges attributes, keeps new ones, one PUT, none when unchanged |
| 1.500 | no library migration drift | the autodetector plans nothing for `oauth2_authcodeflow` under lex's `BigAutoField` |

**Why a regression matters:** a wrong middleware order or issuer signs people out (or keeps them out); permissive CORS lets any website act as the signed-in user; an unparsed `LEX_INSTANCE_HOSTS` breaks the dashboard hand-off; and a sign-out that does not reach the log is invisible to the alerts built on it.

**Allocation note:** Letter bd: az, ba and bb are held by in-flight branches (see [1bc](#batch-1bc--deployed-secret-key-session-lifetime-and-pkce-)).

---

### Batch 1be — Streamlit dashboards sign in through the app (the Django-anchored hand-off) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.501 – 1.540 (1.533 – 1.540: security review fixes, 2026-11-06) |
| Type | U (1.501–1.502, 1.512–1.514, 1.519, 1.521–1.528), I (1.503–1.511, 1.515–1.518, 1.520, 1.533–1.540 transactional; 1.529–1.532) |
| Files covered | `lex/proxy.py` (hand-off section: `_start_handoff`, `auth_handoff`, `auth_required`, `_handoff_auth`, `_proxy_with_handoff`, `_ws_refuse`, `_send_upstream`; `proxy`, `ws_proxy`/`_ws_relay`, `internal_token`, `adopt_token`, `login`, `auth_callback`, `oauth2_logout`, `oauth2_sign_out`, `_PROXY_OWNED_HEADERS`, `LEX_STREAMLIT_AUTH`, `_build_token_store`), `lex/authentication/dashboards/` (new: `modes`, `cookies`, `sites`, `handoff`, `broker`), `lex/authentication/models/_dashboards.py` + `migrations/0002_dashboard_handoff.py` (new), `lex/authentication/views/handoff_views.py` (new, through the round trip), `lex/authentication/management/commands/prune_dashboard_sessions.py` (new), `lex/streamlit_app.py` (`_pull_from_proxy`, `renew_access_token`, `needs_session_recovery`, `render_session_recovery`, `_sync_tokens_from_headers`, `_logout_href`; `_post_refresh` deleted), `lex/lex_app/streamlit/embed.py` (`validated_lex_app_origin`, `remember_lex_app_origin`, `_resolve_base_url`), both component `frontend/index.html`, `lex/bin/lex.py` (`_check_streamlit_auth_env`), `lex/lex_app/settings.py` (`_instance_app_hosts`, `ALLOWED_HOSTS`, exempt patterns). Review fixes: `lex/proxy.py` (`_ws_origin_allowed`, `auth_status` + `/auth/status`, `_handle_standing_blocking`, `_read_handle_any`, `_resolve_handle_blocking(can_set_cookie)`, `_relative`, `_current_relative_path`, dual fall-through in `proxy` and `internal_token`), `lex/authentication/dashboards/sites.py` (`local_path`), `broker.py` (`standing_problem`), `handoff.py` (`select_for_update(of=("self",))`), `lex/streamlit_app.py` (`_in_dashboard_document`, `_handoff_recovery_js`, `_auth_watch_js`, `render_theme_follower`) |
| Test file | `lex/test_project/tests/init/test_1be_streamlit_handoff.py` |
| Test classes | `TestCluster01be_HandoffStart` (1.501–1.502), `TestCluster01be_HandoffRoundTrip` (1.503–1.507, 1.509–1.511, 1.515–1.516), `TestCluster01be_KeycloakOutage` (1.508), `TestCluster01be_BlockedCookiesAndPages` (1.512–1.514), `TestCluster01be_Modes` (1.517–1.518, 1.520), `TestCluster01be_LegacyAndFlag` (1.519, 1.521–1.522), `TestCluster01be_StreamlitProcess` (1.523–1.526), `TestCluster01be_Frames` (1.527–1.528), `TestCluster01be_Housekeeping` (1.529–1.530), `TestCluster01be_SettingsAndMigration` (1.531–1.532), `TestCluster01be_ReviewHardening` (1.533–1.540) |
| Fixtures | the real proxy app through Starlette's `TestClient` (https dashboard host) and the real app through Django's test client (app host) with committed rows (`TransactionTestCase`), signed in with `lex.lex_app.tests.auth.sign_in`; `_upstream_send` / `ws_connect` stand in for Streamlit and record every header as sent; django-oauth2-codeflow's `FakeOP` answers 503 for the outage; fresh interpreters for the settings and the autodetector; `CaptureQueriesContext` for the redeem's lock (1.536); `_JS_HARNESS` runs the component scripts of 1.524 and 1.539 under node (when installed) against a stand-in of the browser's frame rules -- a sandboxed frame is refused when it navigates its parent, a `<script>` it adds to the same-origin dashboard document runs as that document's own |
| Tests landed | **40 pass / 0 fail** (32 + 8 review scenarios) |
| Also changed | 1ac 1.238 (guards the observable boundary -- no POST to Keycloak -- instead of patching the deleted `_post_refresh`) and 1ac 1.239 (now asserts the plan's replacement: a silent proxy is an outage, never a refresh of Streamlit's own). Both asserted the behaviour plan §6.3.5 removes. Review: 1.524 rewritten -- it pinned the recovery script navigating the dashboard document from its component frame (`d.location.replace`), which a browser refuses for a sandboxed frame; it now asserts the navigation the user gets, run against that rule. |
| Mutation check | accepting any `state`, forwarding client identity headers, no blocked-cookie stop, no `Partitioned`, treating 503 as ended, a broker that never revokes, ignoring the frontend's `lex_app_origin`: each fails its scenario (1.505, 1.520, 1.512, 1.501/1.503, 1.523, 1.507/1.509, 1.502). Review fixes, 15 mutants, all killed: no Origin check / only with the hand-off (1.533); no handle clear on the legacy answer / pull 401 on a dead handle in dual (1.534); raw path in `_relative` / in the legacy strip (1.535); `select_for_update()` without `of` (1.536); the pull claiming the re-set (1.537); unprefixed cookie read on https (1.538); `/auth/status` 200 for an ended binding, no watcher, a watcher ignoring 401, the watcher run in the component frame, the old recovery navigating its parent (1.539, 1.524); a broker ignoring `is_active` (1.540) |
| Not exercised | the JSON 503 and the WebSocket close 4503 on a database outage (the document's 503 page is, in 1.535), the plain-http cookie names end to end in a browser, the storage-access button's behaviour, the watcher in a real browser (node runs it against a stand-in; skipped where node is missing) |
| Review notes | Accepted without change (finding 10): concurrent hand-offs rewrite the whole nonce cookie -- at most five nonces, the frozen `__Host-lex_dash_hs` name kept, a lost nonce costs one more hand-off (1.512's counter). Finding 6 residual: the redeem's read and lock no longer touch the sign-in row, but its commit's deferred foreign-key check takes `FOR KEY SHARE` on it, which waits while the library holds the row `FOR UPDATE` across a refresh (measured: waits at `COMMIT` for the hold; 0.04 s when the holder uses `FOR NO KEY UPDATE`) -- the library's lock mode, reported to the coordinator. |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-surfaces-streamlit.md) |

**Why a regression matters:** see [cluster.md § 1be](cluster.md). **Known bugs:** BUG-037 (resolved here; 1.520 is its regression gate), BUG-039 (cross-site WebSocket hijacking, shipped in v2.4.2; resolved, 1.533), BUG-040 (open redirect from the dashboard host, shipped in v2.4.2; resolved, 1.535).

---

### Batch 1bf — MCP embeds sign in with a single-use ticket, into a read-only cookie ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.541 – 1.556 (1.554 – 1.556: security review fixes, 2026-11-06; reserved to 1.560, 1.557 – 1.560 unused) |
| Type | I (transactional: the MCP tool issues tickets from a worker thread) |
| Files covered | `lex/core/middleware/embed_token_auth.py` (removed: 1.546 pins that a token in `?auth_token=` or an embed page's `Referer` signs nobody in), `lex/authentication/dashboards/embed.py` (new: `issue_embed_ticket`, `EmbedTicketMiddleware`; review: `_without_ticket` on-origin, GET-only redeem, the holder's handle passed to the redeem), `lex/authentication/dashboards/handoff.py` (`aud="embed"`; review: `holder_handle`), `lex/authentication/dashboards/broker.py`, `lex/authentication/dashboards/sites.py` (`local_path`), `lex/mcp_server/tools/embed.py` (`_embed_view_inner`, `_issue_embed_ticket`; review: `ToolResult` with the ticketed URL in `_meta`, the widget's `metaUrl`), `lex/lex_app/settings.py` (`MIDDLEWARE`) |
| Test file | `lex/test_project/tests/init/test_1bf_mcp_embed_ticket.py` |
| Test classes | `TestCluster01bf_TicketIssue` (1.541, 1.550), `TestCluster01bf_Redemption` (1.542–1.545, 1.553–1.556), `TestCluster01bf_TheCookie` (1.546–1.549, 1.552), `TestCluster01bf_McpTool` (1.551) |
| Fixtures | `FakeOP` signs the MCP tokens (and serves the JWKS they are validated with); Keycloak UMA stubbed (`stub_keycloak_uma`); the app through Django's test client on its own host; for 1.551, stand-ins for exactly the names the tool imports from modules this line does not ship (`lex.mcp_server.config/registry/context` live on an unmerged branch; `fastmcp` and the MCP SDK are not installed) -- the real ones are used when present |
| Tests landed | **16 pass / 0 fail** (13 + 3 review scenarios; 1.551 rewritten: the ticketed URL now lives only in the result's `_meta`, the stand-in `fastmcp.tools` gained `ToolResult`) |
| Mutation check | writes allowed, a cookie that outlives the token, the ticket left in the URL, no token validation, the tool forwarding `auth_token`: each fails its scenario (1.547, 1.542, 1.542, 1.550, 1.551). Review fixes, 4 mutants, all killed: the raw request path in the ticket redirect (1.554), revoking on the holder's own replay (1.555), HEAD redeeming (1.556), the ticketed URL in the model-visible structured content (1.551) |
| Review notes | Accepted without change (finding 13c): a ticket is not bound to a browser -- whoever opens the embed URL within its 60 seconds gets the read-only embed. The URL is only ever in the widget's `_meta`, single-use, and a second presenter revokes the embed. |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-surfaces-streamlit.md) |

**Why a regression matters:** see [cluster.md § 1bf](cluster.md).

---

### Batch 1bg — Reflex dashboards stay signed in as long as Keycloak keeps the session — retired 2026-11-19

> **Retired 2026-11-19** with Reflex Enterprise (Reflex without Reflex Enterprise, PR B): the refresh guard, cross-tab single flight, cookie lifetimes and back-channel receiver served Reflex Enterprise's token cookies; Django's sign-in now refreshes once per sign-in and ends it ([1bx](#batch-1bx)). BUG-048 to BUG-051 note it. Test file removed; letter and scenarios kept.

| Property | Value |
| --- | --- |
| Scenario range | 1.561 – 1.591 (reserved to 1.600; 1.592 – 1.600 unused); 1.580 – 1.591 and the revised 1.566 from the security review of 2026-11-06 |
| Type | U (1.561–1.576, 1.580–1.591), I (1.577–1.579) |
| Files covered | `lex/lex_app/reflex/auth.py` (`LexKeycloakAuthState._verify_jwt`, `_validate_tokens`, `_refresh_access_token`, `_lex_try_refresh`, `_lex_refresh`, `_lex_refresh_with_keycloak`, `_lex_token_request`, `_lex_adopt`, `_lex_sign_out`, `_fetch_userinfo`, `_get_userinfo`, `_reset_auth`; since the review also `_lex_verify`, `do_access_token_refresh`, `_lex_check_token_set`, `_lex_kept`, `_lex_withhold_identity`, `_lex_expire_identity`, `_lex_recovered`, `_lex_userinfo_unavailable`, `start_login`, `_lex_uma_permissions`, the `sign_in_unavailable` var, `SignInExpiryMiddleware`, `lex_login_page`, `current_access_token`; `RefreshResult`, `UserinfoStatusError`; the 30-day cookie patch around the class), `lex/lex_app/reflex/_token_guard.py` (new; since the review the bounded `LocalStore`, the lock heartbeat `extend_if`, `may_refetch_keys`, the sealed-for payload, the cooldown's lower bound), `lex/lex_app/reflex/backchannel.py` (new), `lex/lex_app/reflex/app.py` (the middleware), `lex/lex_app/urls.py` (the receiver's import) |
| Test file | `lex/test_project/tests/init/test_1bg_reflex_session_hardening.py` |
| Test classes | `TestCluster01bg_AtHash` (1.561–1.562), `TestCluster01bg_RefreshErrors` (1.563–1.568), `TestCluster01bg_SingleFlight` (1.569–1.571), `TestCluster01bg_Cooldown` (1.572), `TestCluster01bg_CookiesAndVersion` (1.573–1.574), `TestCluster01bg_RedisFallback` (1.575), `TestCluster01bg_AcrossReplicas` (1.576, opt-in Redis), `TestCluster01bg_BackChannel` (1.577–1.579; 1.579 opt-in Redis), `TestCluster01bg_ReviewExpiredSignIn` (1.580–1.581), `TestCluster01bg_ReviewForeignProfile` (1.582), `TestCluster01bg_ReviewVerification` (1.583–1.584), `TestCluster01bg_ReviewRetries` (1.585–1.586), `TestCluster01bg_ReviewStore` (1.587–1.589), `TestCluster01bg_ReviewAudit` (1.590), `TestCluster01bg_ReviewLockAcrossReplicas` (1.591, opt-in Redis) |
| Fixtures | Keycloak faked at the plugin's HTTP-client boundary (`reflex_enterprise.auth.oidc.config.DEFAULT_HTTP_CLIENT`: discovery, JWKS, token and userinfo endpoints, scripted answers and delays; tokens signed with django-oauth2-codeflow's test key, so FakeOP and the dashboard agree); the browser recorded at `chain_event_out_of_band` / `call_event_from_computed_var`; the cookie-sync route registration stubbed (it lives on the running app); the guard's clocks movable; one Reflex root state per tab; `CELERY_BROKER_URL` overridden (none, an unreachable port, or `LEX_REFLEX_REDIS_TEST_URL`); FakeOP + the real `/oidc/backchannel-logout` for 1.577–1.579; since the review: lex's per-event middleware run before each simulated event, tokens issued with a chosen `iat` or signed with a second RSA key (`kid` `rotated`), the plugin's audit sink (`emit_audit`) recorded with its plugin guard patched, the lock's lifetime patched to 0.2 s |
| Tests landed | **28 pass / 3 skipped** in a default run (1.576, 1.579 and 1.591 need `LEX_REFLEX_REDIS_TEST_URL`); **31 pass / 0 fail** with it (`redis://127.0.0.1:6391/0`), 19 subtests. Before the review fixes: 17/2 and 19/0 |
| Mutation check | Removing the overrides one at a time (a throwaway pytest plugin) fails: `_verify_jwt` → 1.561–1.562; `_refresh_access_token`/`_get_userinfo`/`_fetch_userinfo` → 1.563–1.568 (all 10 subtests), 1.572, 1.577; `_validate_tokens` → 1.577. Review fixes, each undone in a scratch copy of the source (never the worktree): per-event middleware a no-op → 1.580; `_lex_access_valid` ignoring expiry → 1.566; `_lex_uma_permissions` or `current_access_token` without the expiry check → 1.566; `start_login` going to Keycloak, or no redirect back on recovery → 1.581; serving any cached profile → 1.582; `leeway=0` → 1.583; no key-set refetch → 1.584; storing without `_lex_check_token_set` → 1.583; no re-arm in `_lex_kept` → 1.585 (all 4 subtests); no lock heartbeat → 1.586; `RedisStore.extend_if` not extending → 1.591 (1.586 still passes: the Redis path is its own); no `for` check in `published` → 1.587; no sub/sid check in `_lex_adopt` → 1.587; no sweep → 1.588; no lower bound on the cooldown → 1.589; the back-channel outcome audited again → 1.590 |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-surfaces-reflex-ws.md) |

**Why a regression matters:** dashboards sign users out during a Keycloak blip, a misconfigured client, a clock skew (BUG-050) or a key rotation (BUG-051), two tabs refresh each other's tokens once a second all day (BUG-048), every tab and replica asks Keycloak for its own refresh, sessions end after 7 days, a dashboard stays signed in after the user was signed out everywhere else, or serves an identity -- possibly another user's -- that Keycloak no longer vouches for.

**Also found:** reflex-enterprise's own `at_hash` check compares the ID token with the value the tab remembered, so it *accepts* an ID token from one exchange with the access token of another (1.562 fails without lex's override). Recorded with the loop as BUG-048; to be reported upstream with the other §6.4 items. The check is made only where the tab remembers an expected value (a tab that has stored tokens itself); a tab that has not checks no `at_hash`, as in the plugin -- accepted in the review (finding 12), BUG-048 says what is guaranteed. The review also found two shipped plugin defects, fixed for lex's provider: ID tokens checked with no clock leeway (BUG-050) and the key set cached for the life of the process (BUG-051).

---

### Batch 1bh — The shipped sign-in helper for tests ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.601 – 1.606 |
| Type | I |
| Files covered | `lex/lex_app/tests/auth.py` (new: `sign_in`, `create_sign_in`, `oidc_test_settings`, `stub_keycloak_uma`, `OIDC_BACKEND`, `UMA_FETCH`); `lex/test_project/tests/_e2e_test_case.py` and `lex/tests/e2e/_e2e_test_case.py` now re-export `OIDC_BACKEND`, `oidc_test_settings` and `sign_in` from it. `sign_in` undoes its settings only while they are still in force (`_override_for_test`), so a test method's own `@override_settings` no longer leaks into later tests (1.606) |
| Test file | `lex/test_project/tests/init/test_1bh_test_sign_in_helper.py` |
| Test classes | `TestCluster01bh_TestSignInHelper` (1.601–1.606) |
| Fixtures | Django `TestCase`; `APIClient`; Keycloak "configured" at `https://keycloak.invalid` through `os.environ` so a lookup that bypassed the stub would fail; a throwaway `unittest.TestCase` whose cleanups the test runs |
| Tests landed | **6 pass / 0 fail**; 1.606 fails against the old cleanup (a throwaway mutant plugin); the re-export users `test_10aa_auth_contract_v2.py` and `test_4y_permission_lookup.py` still pass (32 pass together, 43 subtests) |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-test-sign-in-helper.md) |

**Why a regression matters:** downstream projects (atrium, armira, customer projects) update their tests for 2.5 with this helper; without it they hand-craft sessions that grant nothing, or call a real Keycloak with test tokens.

---

### Batch 1bi — The lex CLI answers a failing management command like manage.py ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.607 – 1.608 |
| Type | I |
| Files covered | `lex/bin/lex.py` (`_forward_to_django`: `CommandError` → `CommandError: <message>` on stderr and the command's exit code; `--traceback` re-raises) |
| Test file | `lex/test_project/tests/init/test_1bi_cli_command_errors.py` |
| Test classes | `TestCluster01bi_CliCommandErrors` (1.607–1.608) |
| Fixtures | Django `SimpleTestCase` (no database); Click's `CliRunner` on the `lex` group after `_install_dynamic_commands()` |
| Tests landed | **2 pass / 0 fail**; 1.607 fails against the old forwarding (a throwaway mutant plugin); `test_1m_cli_commands.py` still passes (9 pass, 1 skip without lex-mcp-local) |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-11-06-lex-cli-command-errors.md) |

**Why a regression matters:** the sign-in operating commands (`lex oidc_check`, `lex oidc_cleanup`, `lex oidc_rotate_keys`, `lex prune_dashboard_sessions`) report problems through `CommandError`; a traceback there reads as a crash in the maintenance job's log.

---

### Batch 1bp — a frame another application put on screen does not get to move that tab ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.667 – 1.673 |
| Type | U |
| Files covered | `lex/proxy.py` (`_frame_may_move_the_tab`, `_frame_breakout_response`, `proxy`), `lex/lex_app/streamlit/Streamlit.py` (`get_html`) at the join |
| Test file | `lex/test_project/tests/init/test_1bp_frame_does_not_take_the_tab.py` |
| Test classes | `TestCluster01bp_FrameDoesNotTakeTheTab` (1.667–1.671), `TestCluster01bp_TheReportedJourney` (1.672–1.673) |
| Fixtures | a hand-built Starlette `Request` parameterised by `Sec-Fetch-Site`; for the join, the real `Streamlit` report's own output driven against `proxy.app` through `TestClient`, with `validate_jwt_token` and `_upstream_send` stubbed (the Keycloak and Streamlit boundaries) |
| Tests landed | **7 pass / 0 fail** |
| Also run | cluster 1z (1.211–1.216), unchanged and still 6 pass — the breakout itself is not narrowed, only who may script the navigation |
### Batch 1az — a machine endpoint on the dashboard origin cannot take over the user's tab ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.415 – 1.422 |
| Type | U |
| Files covered | `lex/proxy.py` (`_NON_LANDING_PREFIXES`, `_NON_LANDING_PATHS`, `_is_landing_path`, `_safe_next_path`, `_unauthenticated_response`, `_is_top_level_document_request`, `theme_relay`, `_THEME_RELAY_CONFIGURED`) |
| Test file | `lex/test_project/tests/init/test_1az_proxy_tab_takeover.py` |
| Test classes | `TestCluster01az_WhereALoginMayReturnTo` (1.415–1.417), `TestCluster01az_WhoMayMoveTheTopWindow` (1.418–1.419), `TestCluster01az_LandingOnTheRelayAnyway` (1.420–1.422) |
| Fixtures | none — a hand-built Starlette `Request` per case; `PUBLIC_URL`, `_THEME_RELAY_CONFIGURED` and `_THEME_RELAY_ALLOWED` patched per scenario, `_put_tokens`/`oauth.oidc` stubbed for the callback |
| Tests landed | **8 pass / 0 fail** (13 subtests) |
### Batch 1ay — `ALLOWED_HOSTS` accepts the instance's own backend Service name ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.410 – 1.414 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (`K8S_NAMESPACE_FILE`, `BACKEND_SERVICE_PREFIX`, `_k8s_namespace`, `_cluster_service_hosts`, `ALLOWED_HOSTS`) |
| Test file | `lex/test_project/tests/init/test_1ay_allowed_hosts_in_cluster.py` |
| Test classes | `TestCluster1ay_AllowedHostsAcceptsTheInClusterService` (1.410–1.414) |
| Fixtures | none — the two helpers are pure, so no settings re-import and no environment patching is needed |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.667 | the shell's frame leaves the shell's tab alone | `Sec-Fetch-Site: same-site` → no `window.top.location`, but still 401, still `lex-auth-required`, still `target="_top"` at this proxy's login |
| 1.668 | an unrelated embedder has no more claim | `Sec-Fetch-Site: cross-site` → the same |
| 1.669 | this origin's own frame still recovers | `Sec-Fetch-Site: same-origin` → the automatic navigation is kept |
| 1.670 | a client that names no embedder is unchanged | no `Sec-Fetch-Site` → pre-existing behaviour, as scenario 1.214 has it for the rest of this routing |
| 1.671 | the embedder check is a header read | `SAME-SITE` / `Cross-Site` refused, `same-origin` and absent allowed |
| 1.672 | the frame the report builds is let in | the URL the real `Streamlit` report produces, loaded as a framed document against the real proxy app, banks the token, is redirected without it and ends on the dashboard's own bytes |
| 1.673 | the fault and the amplifier, observed together | the URL the report produces with no session token still 401s, and that 401 moves no window |

**Why a regression matters:** this is the amplifier, not the fault. Batch 10r stops the Streamlit report framing its dashboard without a credential, so the 401 that started the reported tab takeover no longer happens — but any frame that cannot authenticate, for any reason, could still move a window belonging to the page the user was reading. LEX-1477 fixed this one path at a time (`/_lex/*`, `/_stcore/*`); naming the embedder fixes the class. The counterweight is 1.669 and 1.670: withhold the navigation from everything and the dashboard's own recovery goes with it.
| 1.415 | a utility path is never a return target | `_safe_next_path` rejects `/_lex/theme-relay`, `/_stcore/health`, `/_stcore/host-config`, `/auth/token`, `/auth/adopt` and `/_lex/theme-relay?mode=dark`; `/`, a deep view and one with query + fragment survive unchanged |
| 1.416 | a stashed utility path lands on `/` | `auth_callback` with `/_lex/theme-relay` in the session redirects to `/` |
| 1.417 | no return target for a top-level utility visit | an unauthenticated `Sec-Fetch-Dest: document` load of `/auth/token` redirects to a bare `/auth/login`, with no `next` |
| 1.418 | a hidden utility frame gets a 401, not a breakout | a framed load of each utility path returns `JSONResponse` 401 whose body carries no `window.top.location` |
| 1.419 | a framed dashboard still breaks out | a framed load of `/?model=Period` is still the 401 HTML breakout, still attempting top-nav, still carrying `next=%2F%3Fmodel%3DPeriod` |
| 1.420 | a top-level visit is sent back to the frontend | with `_THEME_RELAY_CONFIGURED`, `theme_relay` answers a `document` request with a 303 to that origin |
| 1.421 | an unconfigured deployment does not bounce to localhost | with nothing configured, the same request is served the relay (200), not redirected |
| 1.422 | the framed relay is untouched | a `Sec-Fetch-Dest: iframe` request still gets 200, the shared storage key, and `frame-ancestors <frontend>` |

**Why a regression matters:** this is LEX-1477, and the shape of the failure is why it took a customer to find it. Nothing errors. The frontend frames a hidden, zero-sized `/_lex/theme-relay`; on 2.1.11 the proxy had no such route (it arrived in 2.2.0 with `8780a119`), so that frame fell into the authenticated catch-all, 401'd, and was handed the frame-breakout page — whose entire purpose is `window.top.location = <login>`. Keycloak had a live SSO session, so the round trip returned immediately and `auth_callback` honoured `next=/_lex/theme-relay`. The result was a tab that, intermittently and only when clicking a model with an embedded dashboard, left the instance frontend and parked on a blank storage document at `https://<instance>-streamlit.../\_lex/theme-relay`, with no error, no log line the user could see, and nothing to connect it to a theme shim.

**Why the route existing is not the fix:** adding `/_lex/theme-relay` removes the one 401 that was observed. It does not remove the rule that produced the outcome — *whatever* 401s inside a frame may move the top window, and *whatever* path 401'd becomes where the browser is left afterwards. Both halves are now bounded by what the path is: only a page may escalate, and only a page may be returned to. 1.419 is the counterweight that keeps the narrowing honest — withhold the breakout from everything and an expired dashboard shows Keycloak's "refused to connect" wall again, which is the failure the breakout was written for in the first place (1.211).

**Frontend twin:** `process-admin-general-client` batch F12u (F12.104–F12.108) closes the same hole from the other side — the relay frame is sandboxed so nothing served there can navigate the tab at all, and the shell attributes a `lex-auth-required` message to the dashboard frame rather than to its origin, which is what let an invisible frame on that origin drive a full-page navigation.
| 1.410 | short Service name accepted | `lex-backend-<instance>` is among the derived hosts |
| 1.411 | the three longer DNS forms accepted | `.<ns>`, `.<ns>.svc` and `.<ns>.svc.cluster.local` are all present |
| 1.412 | nothing added off-cluster | no instance identifier yields `[]`, whatever the namespace says |
| 1.413 | unreadable namespace degrades | an empty namespace yields exactly the short name, and does not raise |
| 1.414 | the namespace file is read safely | `_k8s_namespace` on a missing path returns `""` rather than raising |

**Why a regression matters:** Django compares `Host:` against `ALLOWED_HOSTS` in `CommonMiddleware`, before any view, permission class or model code runs — so a Service name missing from the list is not a permission failure and not a routing failure, it is a `DisallowedHost` with nothing downstream reached. It breaks every service-to-service call an instance makes while leaving the browser-facing surface working, which is exactly how it was found: on a customer instance on 2026-10-25 every file write failed with `OSError` and the backend logged `Invalid HTTP_HOST header: 'lex-backend-armira-self-servi-prod-1492:7000'`, with a valid registered API key and a correct route. `POD_IP`, already in the list, does not cover it — it is the reading pod's own address, so it says nothing about how other pods address the backend by name, and a caller has no way to learn it.

**Why the namespace comes from a file:** `POD_NAMESPACE` is not set in the platform's pods (measured), while `/var/run/secrets/kubernetes.io/serviceaccount/namespace` is mounted in every pod with a service account. Reading it introduces no new required environment variable — which matters here, because `settings.py` already refuses to start when `DOMAIN_HOSTED` is missing, and a second such variable would turn a missing value into a crash-loop rather than a fix.

---

### Batch 1bj — the initial-data load survives restarts and a second server process ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.609 – 1.618 |
| Type | I (1.614 runs a child interpreter) |
| Files covered | `lex/lex_app/initial_data_load.py` (new: lock, state record, decision, non-daemon thread), `lex/lex_app/apps.py` (`async_ready` hands the decision and the load to it) |
| Test file | `lex/test_project/tests/init/test_1bj_initial_data_load_restart.py` |
| Test classes | `TestCluster01bj_StartupEntryPoint` (1.609, 1.613), `TestCluster01bj_OneProcessAtATime` (1.610–1.612, 1.617), `TestCluster01bj_RecordedOutcome` (1.615, 1.616, 1.618), `TestCluster01bj_ProcessExit` (1.614) |
| Fixtures | `SimpleItem` (E2E table); `_Seed`, a `ProcessAdminTestCase` naming `SimpleItem`; `_FakeLoad` in place of `load_data` (the seed body is 1f/1i's); `_OtherProcess`, a second database session holding the real advisory lock and either releasing it or dying with it |
| Tests landed | **10 pass / 0 fail**; 1av, 1c/1f/1i and 8j unchanged (55 pass / 1 skip together) |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.609 | not a daemon | `async_ready`, called from the daemon `lex-async-ready` thread, loads in `lex-initial-data-load` with `daemon=False`, outside `WaitForTasks`; state `completed` |
| 1.610 | the second process waits | while another session holds the lock and loads, nothing is decided here; afterwards the normal skip, the seed exists once |
| 1.611 | a killed load is reported | the holder writes part of the seed and its session dies: `interrupted`, an ERROR naming the recovery, no load over the partial rows |
| 1.612 | a stopping process gives up waiting | `abandoned`, nothing loaded |
| 1.613 | MQ/Worker | the load runs inside `WaitForTasks` in the same non-daemon thread |
| 1.614 | the process waits for the load | a child interpreter whose main thread returns mid-load exits only after the load finished |
| 1.615 | a failed load | recorded `failed` with the error; the next start reports it, does not load over it |
| 1.616 | died before writing | `running` with no rows is loaded again and recorded `completed` |
| 1.617 | lock released | another session can take the lock after a load |
| 1.618 | existing data | with no record or a `completed` one: the ordinary skip, no ERROR |

**Why a regression matters:** the "all referenced models empty" gate cannot tell a finished seed from half of one. A load cut off by a restart left production with 89 of 179 companies and a log that said the data already existed; every later start skipped the load.

---

### Batch 1bk — the Streamlit handshake stays inside everyone's header budget ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.619 – 1.625 |
| Type | U |
| Files covered | `lex/ws_header_limits.py` (new: `apply_websocket_header_limits`, `websockets_line_limit_shortfall`), `lex/bin/lex.py` (import order, the `streamlit` command's launch diagnostic), `lex/proxy.py` (import order, credential forwarding in `ws_proxy` and `proxy`), `lex/streamlit_app.py` (`bearer_from_headers`, read-side only) |
| Test file | `lex/test_project/tests/init/test_1bk_streamlit_header_budget.py` |
| Test classes | `TestCluster01bk_WebsocketHeaderLimit` (1.619–1.621, 1.625), `TestCluster01bk_SingleCredentialHeader` (1.622–1.624) |
| Fixtures | `_RawCapturingUpstream` — like 1ac's stub but keeping header names verbatim, because the case-only duplicate this batch guards is invisible to a dict keyed on the lowered name; `_FakeUpstreamWS` keeping the handshake list as sent; `patch.object(websockets.http11, "MAX_LINE", …)` for the shortfall check, so the result does not depend on the installed release |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.619 | the ceiling is the default | with the variable unset, importing `lex.ws_header_limits` leaves `WEBSOCKETS_MAX_LINE_LENGTH` at `65536`, and at least twice the library's own 8192 |
| 1.620 | an operator's value wins | a pre-set `131072` survives the import — `setdefault`, not `set` |
| 1.621 | applied in time | `from lex import ws_header_limits` precedes `import uvicorn` in `lex/bin/lex.py` and `from websockets …` in `lex/proxy.py`, matched as import statements so `starlette.websockets` cannot anchor the check |
| 1.622 | one copy, HTTP | a proxied `/dashboard` request carries `Authorization: Bearer <token>` exactly once and neither `X-Forwarded-Access-Token` nor `X-Streamlit-Access-Token` |
| 1.623 | not two Authorizations | a request sending its own `Authorization` reaches the upstream with only the proxy's |
| 1.624 | one copy, handshake | the `/_stcore/stream` handshake carries one `Authorization`, no retired header, and `lex.streamlit_app.bearer_from_headers` returns that token |
| 1.625 | a ceiling not in force says so | `MAX_LINE` 8192 against a configured 65536 returns a sentence naming both; equal values return `None` |

**Why a regression matters:** the failure mode is an open socket and a blank page. uvicorn's `websockets-sansio` handler neither answers nor closes on a handshake parse error, so LEX-894 (instance lex-1483) presented as a Streamlit dashboard that never loaded, with nothing in any log naming a header. The root cause — a per-instance Keycloak client running with full scope, so every instance's token carried every other instance's roles and audience — is removed in LEX_TERRAFORM_MODULES (`modules/lex-keycloak`, LEX-898). This batch is the floor under it, and each half can be broken silently: a ceiling applied one import too late has no effect, a websockets below 13 ignores it outright, and either leaves the environment saying 65536 while the handshake still dies at 8 KB.

**Mutations against lex-app-v2 (the pre-change tree):** 1.621 fails for both modules, 1.622 and 1.624 fail for both retired headers. 1.619, 1.620, 1.623 and 1.625 pass there — 1.623 pins an invariant (`authorization` in `_HOP_BY_HOP`) that this change makes load-bearing rather than one it introduces, and the other three guard the new module against being weakened later.

**Letter:** taken at `bk` / 1.619–1.625 rather than the next free pair on `lex-app-v2` (`ay` / 1.410): open branches hold `ay` through `bj`, and the same step was taken for `bj`.

---

### Batch 1bl — more than one hostname per instance ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.630 – 1.631 |
| Type | U |
| Files covered | `lex/lex_app/settings.py` (`_lex_extra_allowed_hosts`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, the `DEPLOYMENT_ENVIRONMENT` guard) |
| Test file | `lex/test_project/tests/init/test_1bl_extra_allowed_hosts.py` |
| Test classes | `TestCluster01bl_ExtraAllowedHosts` (1.630–1.631) |
| Fixtures | none — each scenario imports the settings module in a fresh interpreter with `PYTHONPATH` on this checkout, because `ALLOWED_HOSTS` is built once at import |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.630 | the extra hosts land in both settings | unset, `ALLOWED_HOSTS` is the four entries it always held and `CSRF_TRUSTED_ORIGINS` the two; set, each name is appended trimmed and without a trailing slash, and reaches `CSRF_TRUSTED_ORIGINS` as `https://host` — a leading-dot host as `https://*.domain`, the only wildcard an origin may be written with; the baseline entries are byte-identical either way |
| 1.631 | a deployed instance refuses `*` | `DEPLOYMENT_ENVIRONMENT` set and `LEX_EXTRA_ALLOWED_HOSTS=*` raises `ImproperlyConfigured` naming the variable; a real hostname imports cleanly; off a deployed instance `*` is not the framework's business and startup is unaffected |

**Why a regression matters:** `DOMAIN_HOSTED` is one name, so before this every other hostname reaching the pod — a customer domain attached but not yet promoted, a vanity host in front of one public route — was routed by the ingress and then refused by Django with a 400 that names nothing. `USE_X_FORWARDED_HOST` is on, so Django judges the host the *client* asked for and no ingress-side rewrite (`upstream-vhost` and friends) can stand in for this list: drop it and the only remaining fix is one canonical hostname per instance. The `*` guard is the other half — a list that may be widened arbitrarily is a Host-header poisoning switch, and it has to fail at startup rather than at review.

---

### Batch 1bm — custom domains: every attached hostname accepted, dashboards framed same-site ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.632 – 1.653 |
| Type | U / I |
| Files covered | `lex/hosts.py` (new: `public_hosts`, `canonical_url`, `registrable_domain`, `same_site_host`, `same_site_url`, `host_of`, `CurrentHostMiddleware`), `lex/lex_app/settings.py` (`ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `MIDDLEWARE`), `lex/proxy.py` (`PUBLIC_HOSTS`, `_requested_host`, `_external_base_url`, `_callback_url`, `_frontend_origins`), `lex/lex_app/streamlit/Streamlit.py`, `lex/lex_app/reflex/Reflex.py`, `lex/lex_app/streamlit/embed.py` (`_resolve_base_url`), `lex/lex_app/reflex/config.py` (`public_origins`, `cors_allowed_origins`), `scripts/check_custom_domain_live.py` |
| Test file | `lex/test_project/tests/init/test_1bm_custom_domain_hosts.py` |
| Test classes | `TestCluster01bm_PublicHostList` (1.632–1.634), `TestCluster01bm_SameSiteDashboard` (1.635–1.639, 1.648, 1.653), `TestCluster01bm_DjangoAcceptsEveryAttachedHost` (1.640–1.641, 1.647), `TestCluster01bm_StreamlitProxyFollowsItsHost` (1.642–1.644), `TestCluster01bm_ReflexAcceptsEveryAttachedHost` (1.645–1.646), `TestCluster01bm_TheSignInRoundTripFollowsItsHost` (1.649–1.651), `TestCluster01bm_ReflexResolvesARewritableBackend` (1.652) |
| Fixtures | `ATTACHED_ENV` / `PLAIN_ENV` — one instance as the Instance Controller leaves it with and without a customer domain attached; `_Env` (set/remove env vars), `_request` (a Starlette request carrying a real `Host`), `_reimport_proxy_with` (the proxy resolves its hosts at import), `_django_host_settings` (a child process, because `ALLOWED_HOSTS` is resolved while `settings.py` imports), `_FakeKeycloak` (an OIDC discovery document on loopback, so authlib builds a real redirect), `_ingress_headers` |
| Tests landed | **22 pass / 0 fail** (21 pass / 1 skip where reflex is not installed) |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.632 | no custom domain, no change | `public_hosts` returns the canonical host alone for all three roles |
| 1.633 | lenient list parsing | spacing, scheme, case, duplicates and a trailing comma normalise away; canonical first |
| 1.634 | registrable domain | plain, deep and `co.uk` names; a customer domain and the platform's are different sites |
| 1.635 | same-site dashboard | the viewer's own registrable domain wins, for Streamlit and Reflex alike |
| 1.636 | fallback is verbatim | an unmatched viewer gets the canonical URL; `http://localhost:8501` / `:8502` keep scheme and port |
| 1.637 | `CurrentHostMiddleware` | the request's host is the default viewer, and `current_host()` is empty again afterwards |
| 1.638 | `Streamlit.get_html` | `src="https://streamlit.app.customer.com/?embed=true"` |
| 1.639 | `Reflex.get_html` | `src="https://reflex.app.customer.com/?lex_embed=1"` |
| 1.640 | Django host settings | both hosts in `ALLOWED_HOSTS`, both as exact `https://` CSRF origins, middleware installed |
| 1.641 | no widening | with no host list, `ALLOWED_HOSTS` is exactly `[DOMAIN_HOSTED, 127.0.0.1, localhost, POD_IP]` |
| 1.642 | callback follows the host | `_external_base_url` and `_callback_url` name the hostname the request arrived on |
| 1.643 | allow-list, not header trust | `Host: evil.example.net` falls back to the canonical URL; `_requested_host` is `""` |
| 1.644 | renewal origins | both app hosts and both dashboard hosts are trusted; `*` is not |
| 1.645 | Reflex CORS | every attached reflex host as an `https://` origin, canonical first |
| 1.646 | local Reflex | `public_origins()` is empty, so no `cors_allowed_origins` is set at all |
| 1.648 | `lex_view()`'s frame | the app host on the dashboard's own registrable domain; the platform host for an instance with no custom domain; an explicit `REACT_APP_URL` wins; a local run keeps `http://localhost:8000` |
| 1.649 | the sign-in round trip | through `TestClient(proxy.app)` with `X-Forwarded-Host`: `redirect_uri` is `https://<that host>/auth/callback`, for the attached host and the canonical one |
| 1.650 | forged forwarded host | the canonical callback is used; `evil.example.net` appears nowhere in the IdP redirect |
| 1.651 | renewal preflight | 204 + explicit `Allow-Origin` + `Allow-Credentials` from `https://app.customer.com`; 403 from a foreign origin |
| 1.652 | Reflex's own resolution | `lex_config().api_url` and `Endpoint.EVENT.get_url()` are on a host listed in the installed `state.js`'s `SAME_DOMAIN_HOSTNAMES`; `cors_allowed_origins` holds both hostnames |
| 1.653 | malformed dashboard URL | a port outside 0-65535, a negative one and a non-numeric one all degrade to `https://dash.example.com` rather than raising |
| 1.647 | hardened instance | `LEX_CORS_ALLOWED_ORIGINS` keeps the operator's entry and gains both of the instance's own origins; the permissive default stays closed |

**Live check:** `scripts/check_custom_domain_live.py` runs a real Streamlit server behind the real proxy under uvicorn, over loopback sockets, with the `X-Forwarded-*` headers the ingress sets — the layer no test here reaches. 12 checks: Streamlit's own health proxied on both hostnames, the frame-breakout page, and the sign-in redirect. On the base commit three of them fail, and they are the bug in full: the breakout sends the top window to the PLATFORM host's sign-in and the `redirect_uri` is the platform callback, so the browser returns to the attached hostname with no cookie and signs in again.

**Mutation check:** making `_requested_host` return `""` and deriving the renewal origins from `DOMAIN_HOSTED` alone — i.e. the code as it was — fails 1.642, 1.644, 1.649 and 1.651. The same four behaviours were also reproduced against the base commit by serving `lex/proxy.py` with uvicorn and reading the `Location` header: a browser on `streamlit.app.customer.com` was sent to the platform callback, and the renewal preflight from `https://app.customer.com` came back 403.

**Why a regression matters:** every failure here is silent and attributed to something else. A hostname absent from `ALLOWED_HOSTS` makes a correctly-delegated customer domain answer "400 Bad Request"; an OIDC callback built from the canonical host while the user is on another one loops the sign-in forever with every step succeeding; a dashboard framed across registrable domains is third-party and has never worked in Safari. The allow-list in 1.643 is the security edge: the request's host feeds the `redirect_uri`, so reading it straight off the header would let anyone able to set `Host` have Keycloak deliver an authorization code elsewhere.

---

### Batch 1bn — Scheduler bootstrap — the clock in the served backend, code-declared schedules, `run_scheduler` ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.654 – 1.664 |
| Type | U + E |
| Files covered | `lex/scheduling/loop.py`, `lex/scheduling/registry.py` (`lex_job`, `sync_code_jobs`), `lex/scheduling/apps.py`, `lex/scheduling/management/commands/run_scheduler.py`, `lex/lex_app/settings.py` (`LEX_SCHEDULER_*`, `INSTALLED_APPS`), `lex/lex_app/asgi.py`, `lex/utilities/config/generic_app_config.py` (untracked history) |
| Test file | `lex/test_project/tests/init/test_1bn_scheduler_bootstrap.py` |
| Test classes | `TestCluster01bn_SchedulerClock` (U, 1.654–1.657), `TestCluster01bn_CodeDeclaredSchedules` (E, 1.658–1.664) |
| Fixtures | `ScheduledJobTestCase`; jobs declared per test with `lex_job(...)` and removed with `unregister` |
| Tests landed | **11 pass / 0 fail** |
| Status | ✅ Complete (2026-11-10) — scheduled jobs, paired with the other five scheduling batches (8f, 7x, 10ac, 6v, 4z, 1bn) |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.654 | only in the served backend | importing `lex_app.asgi` in a test run starts nothing; a served start starts one thread; a second start returns it; it stops |
| 1.655 | off is said out loud | `LEX_SCHEDULER_ENABLED=false` → no clock and a WARNING that no scheduled job will run |
| 1.656 | on by default | enabled, 30 s tick, one-hour grace without any `LEX_SCHEDULER_*` variable |
| 1.657 | survives failing passes | two raising ticks are logged and the clock keeps ticking |
| 1.658 | a declaration becomes a row | `managed_by_code`, function target, cron, zone, kwargs, enabled, scheduled, no run-as |
| 1.659 | code owns the schedule, the user the switch | a changed cron is applied; a user's `enabled=false` is kept |
| 1.660 | a removed declaration | switched off (not deleted) with a message; declared again, switched back on |
| 1.661 | a person's schedule is never taken over | same name created in the app: unchanged, conflict reported |
| 1.662 | one function per name | a second function is refused naming it; the same function again is fine; a never-firing schedule is refused |
| 1.663 | `run_scheduler --once` | syncs declarations, starts the due job, waits for it (SUCCESS), reports `fired=[...]` |
| 1.664 | registered, not history-tracked | in `processAdminSite.registered_models`; no `history` |

**Why a regression matters:** Instances with no scheduler, clocks in the wrong processes, declared schedules drifting from the code, or a history table growing on every run.

**Allocation note:** Allocated past the letters and scenario IDs that in-flight branches (feat/ext-*, feat/auth-long-sessions) already hold: the first letter absent from lex-app-v2 and every remote branch, and a range above the highest max_scenario on any branch, so neither side has to renumber on merge. Design: [`docs/superpowers/plans/2026-11-10-scheduled-jobs.md`](../../../../../docs/superpowers/plans/2026-11-10-scheduled-jobs.md).

### Batch 1bq — `lex init` writes migrations for the project only, never into site-packages ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.674 – 1.680 |
| Type | U / I |
| Files covered | `lex/lex_app/management/commands/init.py` (`project_app_label`, `execute_migrations`), `lex/lex_app/settings.py` (`INSTALLED_APPS`, `MIGRATION_MODULES`) |
| Test file | `lex/test_project/tests/init/test_1bq_init_project_only_migrations.py` |
| Test classes | `TestCluster01bq_InitMakesProjectMigrationsOnly` (1.674–1.676), `TestCluster01bq_NoAppDriftsFromItsMigrations` (1.677, 1.679, 1.680), `TestCluster01bq_ApiKeysFitTheirColumns` (1.678) |
| Fixtures | none; 1.677 runs `lex makemigrations --check --dry-run` in a child process, as 1.147 does; 1.678 uses the test database |
| Tests landed | **8 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.674 | no file in site-packages | with a project app installed, `lex init`'s migration step asks makemigrations about that app only; `project_app_label()` is the project folder's app when settings installed it |
| 1.675 | the command line still decides | an app named on `--makemigrations-args` is what makemigrations gets |
| 1.676 | a framework checkout | without a project app, makemigrations runs for every app, as before |
| 1.677 | no app drifts | `lex makemigrations --check --dry-run` for every installed app finds nothing to generate |
| 1.678 | wide enough for a real key | an API key created on the migrated test database is stored with its 145-character id and found again by its key |
| 1.679 | one history, not two | lex overrides none of the api-key fork's migrations (no `MIGRATION_MODULES` entry), and the fork's own `0002` widens `id` and `hashed_key` to 150 under the name `lex init` used to generate |
| 1.680 | the id its migrations made | oauth2_authcodeflow's app config (django-oauth2-codeflow 3.0's own) pins `default_auto_field` to `AutoField`, and `BlacklistedToken`'s id is one |

**Why the api-key widening matters.** Making migrations for the project only would leave new databases
with the fork's 100-character columns if nothing else widened them, and its keys need 145: an 8-character
prefix, a dot, and `"sha512$$"` before a 128-character digest. The widening used to come from the file
`lex init` wrote into site-packages. On its branch this batch shipped it from lex (`lex/lex_app/vendored`,
through `MIGRATION_MODULES`) and pinned oauth2_authcodeflow's `default_auto_field` with an app config of
its own. lex-app-v2 meanwhile moved to exc-djangorestframework-api-key 1.0.1, which ships the same
`0002_alter_apikey_hashed_key_alter_apikey_id`, and to django-oauth2-codeflow 3.0, whose app config pins
`AutoField`; so the merge dropped the vendored package, as its own note asked once the fork shipped the
widening. Keeping the override would have hidden the fork's next migration.

**Mutations (on the branch): four of four fail a test.** Not scoping makemigrations fails 1.674. Scoping
it even when the caller named an app fails 1.675. Dropping the widening fails 1.677 and 1.678, on a fresh
test database: the reused one keeps the widened columns.

**Allocation note:** Allocated as 1av (1.386 – 1.392) on its branch; renumbered on merging lex-app-v2,
where 1av was taken, to the first letter absent from lex-app-v2 and every remote branch, and a range
above the highest `max_scenario` on any branch.


## Batch 1br — lex_view filters and on_filter (1.681-1.689)

- **Type:** U
- **Files covered:** `lex/lex_app/streamlit/embed.py`, `lex/lex_app/streamlit/_lex_view_component/__init__.py`,
  `lex/lex_app/streamlit/_lex_view_component/frontend/filter_sync.js`, `…/frontend/index.html`
- **Test file:** `lex/test_project/tests/init/test_1br_lex_view_filters.py`
- **Test classes:** `TestCluster1ay_LexViewFilters`
- **Fixtures:** none
- **Status:** complete — 12 pass (node-dependent tests skip without node)
- **Final review (1.688-1.689):** every frame load resets what the frame is taken to hold to the
  filters its URL carried (`filter_sync.afterLoad`, run from one persistent load handler), so the
  host's filters are re-posted when they differ; a filters token over 2,000 characters stays out of
  the URL (`url_filters_token`) and is delivered by message only.
- **Native column filters (2026-10-20, spec Amendment 18):** the filter bar is withdrawn, so
  `lex_view` has no `filter_bar` parameter and sends no `filter_bar` URL parameter. 1.683 now pins
  that passing `filter_bar` is a `TypeError`, as for any unknown keyword, before anything renders (it
  pinned `filter_bar=a,b` in the URL); 1.685 lost its two `filter_bar` cases. 14 → 12 tests. See the
  [session note](../../progress/sessions/2026-10-20-native-column-filters.md).

---

### Batch 1bs — `LEX_EMBED_ALLOWED_ORIGINS`: named sites only may embed lex-app and use its filter bridge ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.690 – 1.694 |
| Type | U |
| Files covered | `lex/runtime_config.py` (`parse_embed_allowed_origins`, `_normalise_origin`, `embed_origins_configured`, `frame_ancestors_directive`, `merge_frame_ancestors`), `lex/lex_app/settings.py` (`LEX_EMBED_ALLOWED_ORIGINS`, `LEX_EMBED_ORIGINS_CONFIGURED`), `lex/core/middleware/embed_xframe.py` (`EmbedXFrameOptionsMiddleware.process_response`), `lex/react/views.py` (`serve_react`'s `config.js` rewrite and its own document responses' `_apply_embed_csp`) |
| Test file | `lex/test_project/tests/init/test_1bs_embed_allowed_origins.py` |
| Test classes | `TestCluster01bs_MiddlewareAddsFrameAncestors` (1.690), `TestCluster01bs_EmptyAllowListIsANoOp` (1.691), `TestCluster01bs_ConfigJsEmbedOrigins` (1.692), `TestCluster01bs_ParseEmbedAllowedOrigins` + `TestCluster01bs_SettingReadsEnvVar` (1.693), `TestCluster01bs_SpaDocumentCsp` (1.694) |
| Fixtures | `config_js_build` / `config_js_build_without_placeholder` / `spa_build` (`tmp_path`-based synthetic build dirs, matching the 1ag config.js/caching tests) |
| Tests landed | **38 pass / 0 fail** (9 subtests) |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.690 | CSP added on every exempt path | `embed=true`, the OIDC path, and the `_lex_embed` cookie path each get `Content-Security-Policy: frame-ancestors 'self' <origins>`; a non-exempt response gets none; merging preserves an existing header's other directives and replaces a stale `frame-ancestors` directive instead of duplicating it; **configured but nothing valid** still gets exactly `frame-ancestors 'self'` (fail closed) |
| 1.691 | Unset/empty changes nothing | With `LEX_EMBED_ALLOWED_ORIGINS == []` and `LEX_EMBED_ORIGINS_CONFIGURED == False`, no CSP is added, `X-Frame-Options` is still stripped, and the session/CSRF cookies are still patched to `SameSite=None; Secure` — the pre-existing contract, untouched |
| 1.692 | `config.js` carries the setting | `window.REACT_APP_EMBED_ALLOWED_ORIGINS` is the comma-joined list escaped with `json.dumps` (never breaking out of the JS string literal, even for an adversarial-looking value), `""` when empty, and absent entirely when the build predates the placeholder |
| 1.693 | Parsing, normalisation, and the real setting | A path, a wildcard, an `ftp` scheme, embedded credentials, a query string, a fragment, and plain garbage are each dropped; one WARNING names every dropped entry from a single call; valid origins are lower-cased and de-duplicated; a stray/trailing comma is skipped without logging; `lex.lex_app.settings.LEX_EMBED_ALLOWED_ORIGINS` is confirmed (via a real module reload, not just the helper) to equal `parse_embed_allowed_origins(os.getenv("LEX_EMBED_ALLOWED_ORIGINS"))`. **Security fix round:** an IDN host is accepted as punycode; a bracketed/IPv6-shaped host, the `[v1.x https:]` injection shape, and hosts carrying `;`, a space, a quote, a backslash or `%` are all rejected; `:443`/`:80` normalise away; a lone trailing `/` is accepted; a duplicate revealed by port-normalisation still collapses to one; a wildcard-only value fails closed with a dedicated WARNING naming the "limited to the app itself" outcome; `embed_origins_configured` and `LEX_EMBED_ORIGINS_CONFIGURED` are each proven directly |
| 1.694 | The SPA document itself carries `frame-ancestors` | The SPA fallback (`index.html`, reached by any client-routed path with no matching file) and a literal, non-fallback `.html`/`.htm` file (`embed.html`, case-insensitively) both get `Content-Security-Policy: frame-ancestors 'self' <origins>`; merging into an existing CSP header on that response preserves its other directives; a hashed asset and `config.js` get none; with the setting unset, the SPA fallback gets no added CSP; **configured but nothing valid** still gets exactly `frame-ancestors 'self'` |

**Settings access, corrected from the original note above.** `serve_react`'s two `LEX_EMBED_*`
lookups now read `django.conf.settings` (imported as `django_settings`), the same as the
middleware — not the plain `lex.lex_app.settings` module `settings.repo_name` still uses a few
lines away in the same function. The original note here said these were "three different
patterns" across the file requiring `mock.patch.object` for `serve_react`'s tests specifically;
that was true before this fix round and is not any more — every `LEX_EMBED_*`-related test in this
file now uses `override_settings`. `django.conf.settings._wrapped` and the `lex.lex_app.settings`
module remain two independently-imported objects in this codebase (confirmed empirically, not an
assumption), which is still why `TestCluster01bs_SettingReadsEnvVar` proves the setting's own
derivation by reloading `lex.lex_app.settings` after patching `os.environ` instead of using
`override_settings` there — that class is specifically testing what the module-level assignment in
`lex/lex_app/settings.py` computes, not what a deployment's `django.conf.settings` holds at
request time.

**Follow-up (1.694, same session): the SPA document, not the unregistered middleware, is what
actually makes lex-app frameable.** `serve_react` is decorated `@xframe_options_exempt`, which
bypasses Django's own `XFrameOptionsMiddleware` for every response that view returns — unrelated to
whether `EmbedXFrameOptionsMiddleware` is registered. A `frame-ancestors` restriction therefore has
to sit on `serve_react`'s own document responses to take effect at all, as this repo ships today.
`merge_frame_ancestors` moved out of the middleware module into `lex/runtime_config.py` (next to
`parse_embed_allowed_origins`, the other function this feature's two call sites share) so both the
middleware and `serve_react` merge the directive the same way instead of duplicating the logic.
`serve_react` applies it to exactly two response shapes — the SPA fallback (`index.html`) and any
other real `.html` file it serves (e.g. `embed.html`, a second Vite entry point) — and nowhere else:
hashed assets and `config.js` are not documents and keep behaving exactly as before. The middleware's
own CSP step (1.690/1.691) is unchanged, since it still matters for any deployment that does register
it.

**Known pre-existing gap (not this batch's scope, corrected from the original note above):**
`EmbedXFrameOptionsMiddleware` is not currently listed in `lex_app/settings.py`'s `MIDDLEWARE`, in
this repo or its docs. The original note here said this meant the middleware's CSP step "has no
effect" without saying what, if anything, filled the gap — in fact `serve_react`'s own
`@xframe_options_exempt` makes the SPA document frameable regardless of the middleware's
registration status, which is why 1.694 was added in the same session to put the restriction on
`serve_react` directly. The middleware remains unregistered and untouched beyond that; flagged for
whoever owns turning the embed feature on end to end at the middleware layer too (session cookies,
CSRF, the OIDC round-trip).

**Fix round 2, same session (security review): `urlsplit` alone was not safe to splice into a
header.** Two independent problems, both in `_normalise_origin`:

1. A non-ASCII host (`müller.de`) either produces non-ASCII header bytes (dropping the whole CSP)
   or, worse, a crafted bracketed host with an embedded space — `https://[v1.x https:]` — joins
   into the directive as a second, bare `https:` CSP source token, widening who may frame lex-app
   to *every* https site. `_normalise_origin` now rejects any bracketed host outright, IDNA-encodes
   the rest (`müller.de` → `xn--mller-kva.de`; a `UnicodeError` drops the entry), and checks the
   ASCII result against a strict lowercase hostname grammar — IDNA's nameprep step does not reject
   `;`, a space, a quote, a backslash or `%`, so the grammar check is what actually blocks them.
   Also added: the scheme's default port (`:443`/`:80`) is dropped to match a browser's own
   `location.origin`, and a lone trailing `/` is accepted (any further path is still invalid) —
   both needed so an origin written either way normalises to the same, single CSP entry.
2. `LEX_EMBED_ALLOWED_ORIGINS` set to only invalid entries (a wildcard, say) parsed to `[]`,
   indistinguishable from unset — framing silently went unrestricted the moment every configured
   origin happened to be invalid. A new `LEX_EMBED_ORIGINS_CONFIGURED` setting
   (`embed_origins_configured` in `runtime_config.py`) distinguishes "unset" from "set but nothing
   validated"; the latter now fails closed to `frame-ancestors 'self'` on both the middleware and
   `serve_react`, via a new shared `frame_ancestors_directive(origins)` (empty `origins` → `'self'`
   only). `parse_embed_allowed_origins` logs this case once, separately from the
   "dropping invalid origin(s)" warning, naming it explicitly ("framing is limited to the app
   itself").

Minors from the same review, folded into this batch rather than a separate one: `merge_frame_ancestors`
now matches a directive's name exactly (its first whitespace-separated token) instead of by prefix,
and documents that it does not support a comma-joined multi-policy CSP value;
`test_1_694_existing_csp_on_spa_fallback_keeps_other_directives`'s docstring now says plainly that
the state it mocks (`static_serve` returning a response with a CSP header already on it) cannot
occur through `serve_react`'s own normal flow — nothing runs between `static_serve` and
`_apply_embed_csp` — and that `_apply_embed_csp`'s own docstring records the real consequence: a
deployment enabling Django's native `SECURE_CSP` would have `ContentSecurityPolicyMiddleware` skip
its own header on every SPA document once this setting is configured, since one is already present
by the time that middleware's response phase runs; `serve_react`'s document check is now
case-insensitive and also matches `.htm`; the two `LEX_EMBED_*` lookups in `serve_react` moved from
the plain `lex.lex_app.settings` module to `django.conf.settings` so `override_settings` reaches
them, and every test that previously needed `mock.patch.object` to work around that was rewritten
to use `override_settings`; the middleware's own "why this CSP step exists" comment (previously
backwards about what stops what) and `settings.py`'s comment (previously naming only the
unregistered middleware as the mechanism) were both corrected.

No scenario IDs were added — 1.690 and 1.694 each gained one "configured but all invalid" case, and
1.693 gained cases for every invalid host shape plus the fail-closed warning and
`embed_origins_configured` itself, all under their existing numbers.

**Fix round 3, same session (re-review): one further host-validation gap.** Python's stdlib IDNA2003
codec and a browser's WHATWG/UTS46 (non-transitional) domain-to-ASCII disagree on four code points —
sharp s, final sigma, ZWJ and ZWNJ — each folding or dropping here in a way a browser does not, so
the two sides compute different Punycode for the same typed host (`fußball.de` → `fussball.de` here,
`xn--fuball-cta.de` in a browser). These previously passed with no warning. `_normalise_origin` now
rejects a host containing any of the four outright, before encoding — dropped and named in the
existing WARNING, no round-trip check added (one would reject valid Punycode input). Also:
`_HOSTNAME_RE` now uses `.fullmatch()` with no anchors instead of `.match()` + a trailing `$`, which
tolerates a newline immediately before it (verified: not independently reachable through
`_normalise_origin` today, since `urlsplit` already strips `\n`/`\r`/`\t` from the whole candidate
string before this point — the fix is correct defensive practice regardless of today's reachability).
Test fix: 1.693's `;`-carrying host case also carried a `*`, so the existing wildcard pre-check was
rejecting it before the grammar check ever ran; changed to a host with only the semicolon. One new
test (`test_1_693_idna_divergent_hosts_are_rejected`); 37 → 38. No new scenario IDs.

### Batch 1bt — RFDS in the Reflex dashboards — configuration, Keycloak permissions, chrome, host bridge ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.695 – 1.706 |
| Type | U |
| Files covered | `lex/lex_app/reflex/design.py`, `access.py`, `shell.py`, `host.py`, `app.py` (`create_app`), `config.py` (`lex_config`), `auth.py` (`LexKeycloakAuthState`'s RFDS mixin, `lex_login_page`'s notice), `_js/lex_host.jsx`, `_js/lex_rfds.css` |
| Test file | `lex/test_project/tests/init/test_1bt_reflex_design_system.py` |
| Test classes | `TestCluster01bt_DesignSystemConfig` (1.695–1.698), `TestCluster01bt_Access` (1.699–1.701), `TestCluster01bt_Chrome` (1.702–1.704), `TestCluster01bt_HostBridge` (1.705), `TestCluster01bt_TheCompiledApp` (1.706) |
| Fixtures | `_reflex_compiled.compiled_app()` (one compile per process, shared with 1aq); node for 1.705 (skipped without it) |
| Tests landed | **12 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.695 | `lex_config` lists RFDS after Radix, with the LEX brand | plugins are AuthPlugin, RadixThemesPlugin, RFDSPlugin in that order; brand is the LEX teal; badge and telemetry off; `rfds.bootstrap.validate_config` finds nothing |
| 1.696 | Locale, currency and time zone come from lex settings | defaults en-US / en / EUR / Django's time-zone default; each `LEX_*` variable overrides its field; a keyword overrides the environment |
| 1.697 | Conformance warns locally, fails in CI | warn by default; error under `CI`; `LEX_RFDS_CONFORMANCE` wins when valid; a typo falls back to the default |
| 1.698 | Only lex-app may frame a dashboard | app origins from `LEX_APP_HOSTS` as https, localhost wildcards locally; a response has no X-Frame-Options, an enforcing `frame-ancestors 'self' <origins>`, RFDS's report-only CSP without it, and nosniff |
| 1.699 | Grants become permission strings, model-wide only | `<app_label>.<Model>:<scope>` per model-wide scope; record-scoped and malformed grants ignored; `permission()` spells the same string and refuses a scope with a colon |
| 1.700 | The resolver answers from fresh grants and fails closed | remembered identity gets its strings; another subject with the same e-mail, an anonymous one, and the same identity after the TTL get nothing |
| 1.701 | `LexAccessState.load` loads grants, then RFDS's `AccessState` | signed-in user's grants remembered and the handler returns `AccessState.load`; nothing remembered for an anonymous visitor |
| 1.702 | `@lex_page` registers a signed-in page that loads grants first | `rx.page` gets the route, title and loads `[LexAccessState.load, own, ui_open]` (the lex.ui opener, carrying the route, ends every lex page's loads); the nav entry carries heading, icon and permission; the function is returned unchanged; a route without `/` is refused |
| 1.703 | Navigation lists the overview, then each heading | overview first, headings in registration order, a re-registered page listed once in its old place, no navigation without pages |
| 1.704 | The chrome hides inside lex-app and follows its theme | the shell, `LexThemeFollower`, the `restored` notice, the embedded class, the title, the logout condition; the CSS hides header/sidebar under the class and sets the navy chrome |
| 1.705 | The browser bridge obeys only lex-app and reports sign-in state | URL mode first, then only the parent's v1 messages at an allowed origin with a known mode; `restored` only after `required`, once, to the parent's exact origin, lex-auth v2 shape |
| 1.706 | The compiled app is RFDS on lex-app's runtime | `rfds.install` ran; identity resolver is `rxe_identity`, permission resolver lex-app's, nav checker set; head carries lex-app's styles; transformers: RfdsApi first, headers with no frame options, the framing policy; the Keycloak provider has RFDS's mixin |

### Batch 1bu — Model data in the Reflex dashboards — through lex-app's own API, as the signed-in user ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.707 – 1.716 |
| Type | U (1.711: I) |
| Files covered | `lex/lex_app/reflex/data.py` (`LexApi`, `lex_api`, `api_base_url`, `wire_value`, `lex_rows`, `column_kind`, `visible_fields`, `lex_columns`, `lex_grid_columns`, `filter_params`, `scope_params`, `LexTableState`, `table_state`, `lex_table`, `LexGridState`, `lex_grid`, `LexRecordState`), `lex/lex_app/reflex/__init__.py` |
| Test file | `lex/test_project/tests/init/test_1bu_reflex_model_data.py` |
| Test classes | `TestCluster01bu_TheApi` (1.707–1.709), `TestCluster01bu_WireAndColumns` (1.710, 1.712, 1.713), `TestCluster01bu_RowsFromAQueryset` (1.711), `TestCluster01bu_TablesGridsAndRecords` (1.714–1.716) |
| Fixtures | a recording HTTP client standing in for lex-app's API; two `Group` rows for 1.711 |
| Tests landed | **10 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.707 | Calls lex-app's own endpoints with the user's token | Bearer token on every call; list/read/create/update(PATCH)/delete on the model-entry routes, which resolve to lex-app's views; page/perPage/ordering and repeated filters in order; operation ids name model and action; both list shapes become `{count, results}` |
| 1.708 | Refusals and outages are errors, never empty data | 403/404/500/unreachable raise `LexApiError` with the status (0 unreachable); 401/403/404 are `denied`; no token, no API |
| 1.709 | `lex_api` uses the session's token and the configured base | the session's access token; base `LEX_API_URL`, else `https://DOMAIN_HOSTED`, else `http://localhost:8000` |
| 1.710 | Values travel as RFDS wire values | Decimals as exact strings, dates ISO, UUIDs strings, related instances their key, NaN `None`, nested values converted; mapping rows keep chosen fields |
| 1.711 | `lex_rows` reads a queryset with `values()` | real rows of chosen fields in queryset order; instances read field by field |
| 1.712 | Columns follow each field's type and skip credentials | id/boolean/instant/text kinds, verbose-name headers, `password` left out, per-field overrides, an unknown field refused; grid columns likewise |
| 1.713 | Table filters become lex list filters | each RFDS operator mapped to lex-app's lookups (`!`, repeated fields, `__gte`/`__lte`, `__icontains`, `__blank`); malformed names dropped; fixed filters skip empties and repeat lists |
| 1.714 | `lex_table` pages, sorts, filters and searches through the API | one state class per distinct table; mount passes the fixed filters as the state var; page 3 of 25, `-name,id`, fixed + table filters + `name__icontains`; wire rows and the total |
| 1.715 | `lex_grid` loads rows once, says when cut, hides on refusal | first page of `GRID_ROW_LIMIT` with fixed filters; wire rows, `truncated`, dataset version bumped; a 403 leaves no rows and `denied` |
| 1.716 | A record dashboard gets only records the API would show | API shows: instance, title, data, `ok`; 403: `None`, `denied`; 404 or no pk: `None`, `missing`; the instance is read only after the API showed the record |

### Batch 1bv — lex-app's pages and controls inside Reflex dashboards — one embed contract for both frameworks ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.717 – 1.723 |
| Type | U |
| Files covered | `lex/lex_app/embed/core.py`, `lex/lex_app/embed/widgets.py`, `lex/lex_app/embed/__init__.py`, `lex/lex_app/streamlit/embed.py` (`lex_view` over the shared core), `lex/lex_app/streamlit/widgets/spec.py` (re-export), `lex/lex_app/reflex/embed.py`, `lex/lex_app/reflex/_js/lex_frame.jsx`, `lex/utilities/config/generic_app_config.py` (`_LEX_PACKAGE_EXCLUDED_DIRS`) |
| Test file | `lex/test_project/tests/init/test_1bv_reflex_lex_view_and_widgets.py` |
| Test classes | `TestCluster01bv_OneContract` (1.717), `TestCluster01bv_ReflexLexView` (1.718–1.720), `TestCluster01bv_ReflexWidgets` (1.721–1.722), `TestCluster01bv_TheFrame` (1.723) |
| Fixtures | node for 1.723 (skipped without it), React's hooks stubbed |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.717 | Streamlit and Reflex open the same URL | both render `build_lex_view_url`'s URL (Reflex adding the filters token Streamlit's shim adds in the browser); old Streamlit imports are the same objects; discovery never imports `lex_app.embed` |
| 1.718 | Handlers ask for their events and receive them | an `on_create` handler sets only `emit_create` and is wired; `on_event` alone sets every flag; none sets nothing; only lex-app's origin is believed |
| 1.719 | A record named by a state var is joined in the browser | a Var path and an f-string embedding a var become a Var URL with the base, the path and the embed query; filters stay the state var, not frozen into the URL |
| 1.720 | lex-app is framed from the app address beside the dashboards | `REACT_APP_URL`; else the app host on the dashboards' registrable domain; else `http://localhost:8000` |
| 1.721 | Widgets post one validated manifest with runtime records | one `/embed.html?embed=true` frame, version-1 manifest, a Var pk, ids unique per widget, `on_status` wired, the default log dialog unless the page handles logs; empty, duplicate-id and unknown-option specs refused at build |
| 1.722 | A calculation's log opens over the page | `open_log` builds a one-widget log manifest for the run and opens the dialog; an incomplete request changes nothing |
| 1.723 | The frame speaks lex-app's contract and trusts only lex-app | JS filters token equals Python's; the page opens on the framing lex-app origin, never a stranger's; each load posts theme then the missing filters; duplicates, foreign origins, other windows and newer versions ignored; widget host gets manifest and theme on ready; status reaches `on_status` |

### Batch 1bw — A consultant's tooling for Reflex dashboards — lex reflex add, lex reflex check, the reference example ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.724 – 1.727 |
| Type | U |
| Files covered | `lex/lex_app/reflex/scaffold.py` (`render`, `add_block`, `check_paths`, `run_check`), `lex/bin/lex.py` (`lex reflex add`, `lex reflex check`, `_reflex_add`), `lex/lex_app/reflex/examples/_reflex_structure.py` |
| Test file | `lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py` |
| Test classes | `TestCluster01bw_Add` (1.724–1.725), `TestCluster01bw_Check` (1.726), `TestCluster01bw_TheExample` (1.727) |
| Fixtures | a temporary project root with `lex.bin.lex.PROJECT_ROOT_DIR` patched; RFDS's CLI recorded for 1.726; the shared compile for 1.727 |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.724 | `add` writes a page and registers it once | `_dashboards/` package and page; `_reflex_structure.py` created with `main()` and the import once; refused without `--force`; `--no-register` and `object_page` leave the structure alone; bad model, block or route exit non-zero |
| 1.725 | Every template is valid and uses only lex-app's surface | each block parses and imports only names `lex.lex_app.reflex` exports; list and overview are `@lex_page` routes gated on read; object page is a `ui.State` dashboard for `reflex_main`, whose suggested import names the project's package; the overview's database read is behind its `@ui.load`'s `requires=` |
| 1.726 | `check` lints the dashboards and runs the doctor | `lint --strict` over the structure module and the package, then `doctor --app-root`; non-zero when either fails; only the doctor when there is nothing to lint |
| 1.727 | The reference example uses every feature and compiles | imports every feature lex-app gives a dashboard (pages, chrome permissions, tables, grid, lex_view with flows, widgets, the ORM bridge) and writes its handlers with lex.ui (loads, actions, the API as the user, refusals, status); its pages are in the compiled app under one navigation heading; the compile ran conformance in strict CI mode |

### Batch 1bx — Reflex dashboards sign in through lex-app's hand-off - no Reflex Enterprise, no Reflex account ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.728 – 1.735 |
| Type | I (transactional; 1.731 and 1.735 partly U) |
| Files covered | `lex/lex_app/reflex/session.py`, `lex/lex_app/reflex/auth.py` (`current_access_token`, `current_permissions`, `has_permission`, `lex_event`, `LexUser`), `lex/lex_app/reflex/access.py` (marker cleanup), `lex/authentication/dashboards` (reflex audience end to end) |
| Test file | `lex/test_project/tests/init/test_1bx_reflex_sign_in_handoff.py` |
| Test classes | `TestCluster01bx_RoundTrip` (1.728), `_SocketGuard` (1.729), `_Middleware` (1.730), `_Checks` (1.731), `_Token` (1.732), `_Pages` (1.733), `_SignOut` (1.734), `_Exports` (1.735) |
| Fixtures | `lex.lex_app.tests.auth.sign_in`; the real `oidc/handoff` view; a recording ASGI app behind `session.transformer`; `reflex.config.get_config` patched to the two development ports |
| Tests landed | **8 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.728 | A dashboard signs in through lex-app's hand-off | /auth/start: nonce cookie, 303 to <app>/oidc/handoff?aud=reflex with return=<backend><next>?lex_hs=1; lex-app's code; /auth/handoff: reflex binding, lex_rdash HttpOnly SameSite=Lax 30 days, nonce cleared, 303 to the frontend port (single port: the backend); https: __Host-, Secure, SameSite=None, Partitioned |
| 1.729 | The event socket carries the digest, never a cookie | /_event (WebSocket and HTTP): no Cookie header, X-Lex-Binding = sha256(handle), a browser's own X-Lex-Binding dropped; other paths keep cookies; POST to a sign-in route passes through |
| 1.730 | Anonymous sessions go through the hand-off, signed-in ones pass | signed in: passes, identity sub/e-mail/name/issuer, cached a minute; anonymous: page load of /auth/start?next=<page>, Reflex's own events pass; ended sign-in found at the next check, binding revoked; failed read = anonymous; lex_hs=2 -> /auth/blocked; marker cleanup keeps the page's own query |
| 1.731 | RFDS sees the user and lex_event checks run first | rfds current_identity = the binding's user; guarded handler denied without the grant, allowed with it; untagged allowed for signed-in users; anonymous -> sign_in; lex_event without auth = plain rx.event |
| 1.732 | The token comes from the sign-in and follows it | current_access_token = the sign-in's token, cached while valid; Keycloak unreachable -> '' and still signed in; ended -> '' and anonymous; no session, no token |
| 1.733 | Without a sign-in lex-app is told and nothing loops | login_required: 401 page posting lex-auth v2 required/surface reflex to lex-app's origins and marking the tab; a code without our nonce never redeemed; a replayed code refused; unlisted host 400; outage 503 retry; /auth/blocked offers its own tab |
| 1.734 | Signing out revokes the binding, then signs out of lex-app | LexUser.sign_out loads /auth/logout; binding revoked (logout), forgotten, cookie deleted, 303 to <app>/oidc/total_logout?next=<dashboard> |
| 1.735 | An export download answers only its owner | the state's principal is the binding's subject; only the owner's request names it; another user's names theirs; no cookie names nobody |

### Batch 1by — Reflex dashboards without Reflex Enterprise - nothing imports it, and lex-app's own one-port proxy ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.736 – 1.739 |
| Type | U |
| Files covered | `requirements.txt`, `lex/lex_app/reflex/` (every module's imports), `lex/lex_app/reflex/devproxy.py` (`enabled`, `FrontendProxy`, `install`), `lex/lex_app/reflex/config.py`, `lex/lex_app/reflex/app.py` |
| Test file | `lex/test_project/tests/init/test_1by_reflex_without_enterprise.py` |
| Test classes | `TestCluster01by_NoEnterprise` (1.736), `_Switch` (1.737), `_Http` (1.738), `_WebSocket` (1.739) |
| Fixtures | a child interpreter refusing Reflex Enterprise; `httpx.MockTransport` for the frontend; a `websockets` sync server with the `vite-hmr` subprotocol |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.736 | Nothing imports Reflex Enterprise or requires it | child interpreter refusing reflex_enterprise and rfds.enterprise: config, every lex.lex_app.reflex module and lex.ui import, the config is Reflex's own, no Enterprise module loaded; requirements pin reflex and rfds==1.0.0, no reflex-enterprise |
| 1.737 | The proxy runs only for a one-port development run | REFLEX_USE_SINGLE_PORT set, development, not backend-only -> on; unset, production or backend-only -> off; install registers the lifespan task only when on |
| 1.738 | Pages pass through to the frontend server | method, path, query, body on; Host the upstream's; hop-by-hop dropped; status, headers, body back; gzip decoded without Content-Encoding; a starting frontend retried with the body every time; never answering -> 502 |
| 1.739 | The hot-reload socket passes through with its subprotocol | accepted with vite-hmr; text and bytes both ways unchanged; no frontend -> the socket is refused |

### Batch 1bz — The LEX look for Reflex dashboards - RFDS dressed in lex-app's design tokens ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.740 – 1.744 |
| Type | U |
| Files covered | `lex/lex_app/reflex/theme.py`, `lex/lex_app/reflex/design.py` (`lex_head_components`, `brand_spec`), `lex/lex_app/reflex/config.py` (`default_color_mode`), `lex/lex_app/reflex/_js/lex_rfds.css` |
| Test file | `lex/test_project/tests/init/test_1bz_reflex_lex_look.py` |
| Test classes | `TestCluster01bz_Tokens` (1.740), `_Precedence` (1.741), `_Accent` (1.742), `_Components` (1.743), `_Pages` (1.744) |
| Fixtures | none; 1.741 reads RFDS's generated `rfds.css` from the installed package |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.740 | Every colour, type and radius is a lex token | per mode: canvas, surfaces, text, link, lines, teal accent and focus, tag statuses, navy chrome, table header, Inter, radii = the tokens; no hex literal in the module's code; a changed token changes the stylesheet |
| 1.741 | lex values outrank RFDS and reach every Radix element | both mode blocks start at html:root:root (above the brand sheet's :root:root) and cover forced-mode regions, dark last; every re-declared variable the theme sets is forwarded from a per-mode copy; every other one RFDS re-declares reads an accent step the theme sets |
| 1.742 | The accent scale is the LEX teal, not RFDS's adjusted brand | indigo-9 = TEAL 500 both modes; hover, accent text, selection washes, on-solid text and page/panel backgrounds from the tokens; the brand passed to RFDS is the same teal |
| 1.743 | Header, navigation, cards and buttons are lex-app's | navy gradient header with teal glow, no stripe; navy side nav, teal wash + 3 px bar on the current page; 16 px cards with soft elevation; buttons 8 px / 600 / no uppercase / hover lift, none with reduced motion; primary navy gradient + white (light), teal gradient + dark text (dark); soft focus ring, no dashed title outline |
| 1.744 | Pages load Inter and the theme and open in light mode | lex_config default_color_mode light; head links Inter and inlines the theme then the embedded layout; the embedded layout file carries no colours |

### Batch 1ca — A local run lets the dashboards' sign-in and sign-out send the browser back to them ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.745 – 1.745 |
| Type | I |
| Files covered | `lex/lex_app/settings.py` (`_LOCAL_DEV_PORTS`, `_local_dev_origins`, `_cors_allowed_origins`, `_redirect_hosts`), `lex/authentication/middleware.py` (`OidcRedirectGuardMiddleware`, through the views) |
| Test file | `lex/test_project/tests/init/test_1ca_local_dashboard_redirects.py` |
| Test classes | `TestCluster01ca_LocalDashboardRedirects` (1.745) |
| Fixtures | `oauth2_authcodeflow.testing.FakeOP`; the allowlists overridden as settings derives them |
| Tests landed | **1 pass / 0 fail** (6 subtests) |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.745 | A local run trusts the dashboards' dev servers and no more | local: 8501, 8502, 8503 and a moved Reflex port trusted on localhost and 127.0.0.1, total_logout and authenticate redirect back to the dashboard; another loopback port and a foreign site still 400 REDIRECT_NOT_ALLOWED; deployed: no localhost; this local run's real settings carry the dashboards' origins |

### Batch 1cb — lex-app's grids and dashboard pages take the page's width ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.746 – 1.746 |
| Type | U |
| Files covered | `lex/lex_app/reflex/data.py` (`lex_grid`), `lex/lex_app/reflex/dashboards.py` (`dashboard_page`) |
| Test file | `lex/test_project/tests/init/test_1cb_reflex_full_width.py` |
| Test classes | `TestCluster01cb_FullWidth` (1.746) |
| Fixtures | none (`lex_grid(Group)`, `dashboard_page` with no models) |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.746 | Grids and dashboards fill the page width | lex_grid's stack stretches its children and RFDS's grid frame stays 100% wide; the stack directly inside the shell's main region stretches, so a project's page, tables and cards take the frame's width |

### Batch 1cc — lex.ui results and thread pools ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.747 – 1.749 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/results.py` (`goto`, `toast`, `download`, `progress`, `Refuse`, `split`, `to_events`), `lex/lex_app/reflex/ui/pools.py` (`size`, `pool`, `run`) |
| Test file | `lex/test_project/tests/init/test_1cc_ui_results_and_pools.py` |
| Test classes | `TestCluster01cc_Results` (1.747–1.748), `TestCluster01cc_Pools` (1.749) |
| Fixtures | none (the test recorder and the pool-size environment variables are patched per test) |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.747 | A handler returns plain values and nothing else | goto, toast and download are results; progress is the progress report, clamped to 0..1; None is nothing; a wrong tone, an empty path, a download of nothing and any other object (a raw Reflex event) are refused with the handler's name |
| 1.748 | Values become browser events and are recorded for tests | an in-app path is Reflex's redirect, another site (a full URL, or //host) a whole-page navigation, the toast RFDS's toast of that tone, the download Reflex's download; with the test recorder on, the values themselves are recorded in order |
| 1.749 | Bodies run on sized pools with the caller's context | functions run off the calling thread on a thread named for their pool, and see the caller's context variables; the sizes come from LEX_UI_ACTION_THREADS and LEX_UI_JOB_THREADS (16 and 8 by default, at least 1); a full pool queues: on a one-thread pool a second call waits its turn and does not fail |

### Batch 1cd — lex.ui guard: requires values, the query block, the acting user ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.750 – 1.752 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/guard.py` (`normalise`, `Requirement`, `granted`, `block_queries`, `UnguardedQuery`, `acting_user`) |
| Test file | `lex/test_project/tests/init/test_1cd_ui_guard.py` |
| Test classes | `TestCluster01cd_Guard` (1.750–1.752) |
| Fixtures | `django.test.TestCase`; `auth.Group` as the guarded model; Keycloak grants as plain dictionaries |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.750 | requires= values and the grant check | nothing declared is None; one value or a list is a tuple; ui.read(Group) holds; a record-scoped edit does not open the model; a list needs all; ui.signed_in always holds; a named resource works like a model; an empty list is refused |
| 1.751 | Queries are refused inside the block and work after it | inside block_queries a query raises UnguardedQuery naming the handler and the two ways out (self.api, requires=); after the block queries work again |
| 1.752 | The acting user is set for audit fields | inside acting_user lex-app's operation context carries a request whose user is authenticated and reads as the username (the e-mail when there is none, the user's id when there is neither); it is restored afterwards |

### Batch 1ce — lex.ui working copy: snapshot, restore, draft, merge ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.753 – 1.755 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/workcopy.py` (`snapshot`, `restore`, `reset`, `Draft`, `merge`) |
| Test file | `lex/test_project/tests/init/test_1ce_ui_working_copy.py` |
| Test classes | `TestCluster01ce_WorkingCopy` (1.753–1.755) |
| Fixtures | small Reflex states (`_Parent`, `_Tickets`) and a plain stand-in for a live state; a value whose `!=` returns a list, as array types do |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.753 | A snapshot holds the state's own fields and restores them | the snapshot holds deep copies of the class's own fields only; restore puts every changed field back; reset sets fields to their class defaults except the kept one; a field holding an uncopyable object makes snapshot raise a TypeError naming it |
| 1.754 | A draft reads and writes fields and reaches helpers | fields behave as on the state; a computed value and an underscore helper work on the draft's values; assigning an undeclared field and calling another event handler are refused with a message that says what to do; so is reading an inherited field |
| 1.755 | Merge writes only what the draft changed | the first merge writes progress and rows and leaves the user's note; the draft then sees the user's note; when both changed the same field the job's value wins; a NaN or a value whose != is not a bool is compared safely, not read as changed |

### Batch 1cf — lex.ui context: user, params, grants, the API as the user ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.756 – 1.758 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/context.py` (`Session`, `Invocation`, `current`, `Api`), `lex/lex_app/reflex/session.py` (`access_token_of`), `lex/lex_app/reflex/auth.py` (`permissions_of`) |
| Test file | `lex/test_project/tests/init/test_1cf_ui_context.py` |
| Test classes | `TestCluster01cf_Context` (1.756–1.758) |
| Fixtures | a stand-in session (user, parameters, grants, a token source); `httpx.MockTransport` recording lex-app's API calls |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.756 | User, params and grants are reachable from the body | the user's four names are there; a parameter reads as an attribute or with get(); a missing one is an AttributeError that says so; can() answers from the grants; outside a handler current() says it only works inside one; on_loop refuses the event loop's own thread, and on a timeout it cancels the coroutine so it never completes |
| 1.757 | The API is called as the user, with a fresh token each time | each call carries the token current at that moment as a bearer token; list sends the filters (a list as repeated values), the search on the model's text field and the ordering, and returns plain rows with the total count, trimmed to the fields asked for; get, create, update and delete hit lex-app's routes and return the row |
| 1.758 | A refusal is an API error and a stand-in replaces the API | 403 raises LexApiError with denied true; no token raises 401; with a stand-in, self.api is the stand-in itself |

### Batch 1cg — ui.State and handler status ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.759 – 1.761 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/state.py` (`State`), `lex/lex_app/reflex/ui/runs.py` (`mark_started`, `mark_progress`, `mark_finished`, `running`, `failed`, `progress_of`, the run registry) |
| Test file | `lex/test_project/tests/init/test_1cg_ui_state_and_status.py` |
| Test classes | `TestCluster01cg_State` (1.759–1.761) |
| Fixtures | a project state inheriting `ui.State` (`_Board`); a stand-in status |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.759 | ui.State adds fields and helpers but no event handlers of its own | ui.State is a real Reflex state with the status fields and the visit counter; user, api, params, can, state and tab_id are properties, not event handlers; the project's state inherits the status fields and keeps only its own as its own; those six names are reserved -- a project state declaring a field (with or without a default), a computed var or a method of one of them is refused when the class is defined, with a message naming the state and the name and saying to rename it |
| 1.760 | Status marks follow a run's life | running lists the rows in flight; progress holds the fraction and the message; finishing removes the row and the progress, records the failure message, and a new start clears the previous failure; the registry lets one run per key and argument in, and a cancel request reaches every run of that handler |
| 1.761 | Views read status with running, failed and progress_of | each is a Reflex expression over ui.State's status fields under the handler's key, with a default when nothing was recorded; the per-row form compares the row as text |

### Batch 1ch — @ui.action: plain synchronous handlers and their failures ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.762 – 1.767 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`action`, `_drive`, `_classify`, `_detail_text`), `lex/lex_app/reflex/ui/testing.py` |
| Test file | `lex/test_project/tests/init/test_1ch_ui_action.py` |
| Test classes | `TestCluster01ch_Action` (1.762–1.767; transactional, the body runs on another thread with its own connection) |
| Fixtures | `django.test.TransactionTestCase`; `Board`, a `ui.State` with the actions under test; `ui.testing`'s harness and recorder; `auth.Group` rows |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.762 | A plain function runs off the loop on the live state | the body ran on an interactive pool thread and saw the user and the parameter; the fields changed and reached the browser; the toast was shown; nothing is running afterwards and no failure is recorded; an async def under @ui.action is refused |
| 1.763 | Running is shown first and each yield flushes | the browser first learns the handler is running, then sees steps=1 before the body has finished, then steps=2; running is cleared at the end; the goto is sent |
| 1.764 | A failure discards what was not shown and hides the detail | the fields are back at what the yield showed; the user gets an error toast with a reference and without the exception's text; the failure message is recorded for the view; the log holds the traceback by default and only the type with LEX_UI_LOG_ERROR_TEXT=false; an uncopyable field, an unresolvable session and a wrong yielded value each end as an unexpected error with nothing left running; a finally in the body runs before the restore. A body that calls another handler of its state (self.reload()) fails the action instead of silently doing nothing: its change is restored, the sibling never ran, and the log names both handlers and says to use an underscore method |
| 1.765 | Refusals and API answers are shown in their own words | a ui.Refuse message is shown as written, as a warning, and its changes are discarded; a 403 is the standard "not permitted" message; a validation error is shown with the API's detail -- lex-app's own refusal body by its detail alone, and a body without one as its fields' name: text pairs, never its code, error or traceback; none is reported as an unexpected error |
| 1.766 | The database is reachable only with requires | without requires= the query is refused and reported as an unexpected error whose log names the handler and the way out; with the grant the count is read; without the grant the body never runs, the user is told they are not permitted, that message is recorded as the failure and nothing is left running |
| 1.767 | The loop stays free and a slow action is called out | the event loop keeps ticking while a body sleeps; a 1.1 s run in development logs a warning that names the handler and says to make it a job; a cancelled body's thread finishes, nothing is left marked running, and a following action on the same tab still runs; a stopped body whose cleanup raises is logged as a warning, not swallowed |

### Batch 1ci — @ui.job: long work on a working copy ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.768 – 1.773 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`job`, `_merge`), `lex/lex_app/reflex/ui/runs.py` (cancel, the run registry) |
| Test file | `lex/test_project/tests/init/test_1ci_ui_job.py` |
| Test classes | `TestCluster01ci_Job` (1.768–1.773) |
| Fixtures | `Export` and `Other`, `ui.State` classes with the jobs under test (bodies held at gates the test releases, and a foreground `hold` that keeps the user's lock); `ui.testing`'s harness; `_ui_harness_aids.py` (a state manager that, like Redis, does not store a block that raised; the lock's holder and waiters) |
| Tests landed | **9 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.768 | The user keeps working and their changes survive | the user's event returns at once; the job saw the old note until its next yield and the new one after it; at the end the job's fields and the user's note both stand; another state the job changed through self.state() is merged too; the toast is shown; the browser got progress at each yield |
| 1.769 | The job wins when both change the same field | a job that sets status and a user who sets it meanwhile: the job's value stands |
| 1.770 | One run at a time per tab and set of arguments | for "a" sent three times rapidly the job ran once and the repeats were ignored; "b" ran beside it; a later "a" after it finished ran again. A call with a different second argument runs beside the first (only the exact same arguments are a repeat) and the view still sees each run under its first argument; on a full one-thread job pool a second job waits its turn instead of failing |
| 1.771 | Cancel takes effect at the next yield | the job stops within a few steps, its finally block runs and it is no longer running; what the stopped body changed in its cleanup is never merged; ui.cancel(handler) is the event for a Cancel button. A job whose task is cancelled while its wind-up waits for the user's lock is still wound up: not left running, its failure recorded, its claim released |
| 1.772 | Stop on leave ends the job when the page changes | a job with stop_on_leave=True stops at its next yield when the user opens another page, and its cleanup runs |
| 1.773 | A failing job keeps what was merged and reports | what was merged at the yield stays, the later change is discarded, an error toast is shown, the failure message is readable with ui.failed, and it is no longer running. A job whose requires= is not met (under a store like Redis) never runs: the user is told they are not permitted, the message is recorded, the warning names the handler and the user's id, nothing is left running |

### Batch 1cj — @ui.load: page loads, the opener, the error page ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.774 – 1.778 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`load`, `page_loads`, `register_page`, `page_of`, `load_id`, `_return_path`), `lex/lex_app/reflex/ui/state.py` (`ui_open`, `error_page`, `check_route`, `check_app_routes`), `lex/lex_app/reflex/ui/testing.py` (`open_tab`, `navigate`, `mount`), `lex/lex_app/reflex/shell.py` (`lex_page`), `lex/lex_app/reflex/app.py` (`create_app`) |
| Test file | `lex/test_project/tests/init/test_1cj_ui_load.py` |
| Test classes | `TestCluster01cj_Load` (1.774–1.778) |
| Fixtures | `Page`, `Twin` (two same-named states in different modules, one in `_ui_load_twin.py`) and `Dash` as the states under test, their bodies held at gates the test releases (no fixed waits: the test awaits the events' futures); `ui.testing`'s harness, tab and mount helpers; `_ui_harness_aids.py` (a state manager that, like Redis, does not store a block that raised; the lock's holder and waiters); a child interpreter for the create_app import-order check |
| Tests landed | **12 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.774 | A load resets the state, then fills it -- once per visit | the rows were emptied before the body ran, then filled from this visit's parameter; the kept field was not reset; the browser saw the empty rows first; within one visit the optional load does not reset what the required one filled, and the next visit's first load clears it. A dashboard's load started on mount lands after the opener (and after a navigation from another page), resets the state at every start, and leaves nothing running |
| 1.775 | The opener starts required loads, then optional ones | the on_load list is split, keeping handlers and leaving a plain Reflex handler in place; the opener takes only the route, checks the permission the page was registered with, starts a new visit, then the required load, then the optional one; a user lacking the permission or an unknown route starts nothing; each same-named twin page starts its own state's load. A load started on mount starts no optional load and leaves the opener's required load pending |
| 1.776 | Stale results are discarded | a result that arrives after the user opened another page is not written; a cancelled required load is not running, stays pending and starts no optional load. A load started on mount is dropped only when a newer run of itself starts (older finishing last or first, same argument overlapping); the newer run keeps its process record; a run whose browser moved on still fills its own state. A load whose task is cancelled while its wind-up waits for the user's lock is still wound up and not left running |
| 1.777 | A failed required load shows the error page, an optional one its region | a required failure records a reference and the page to retry (its query string kept, never the error page), sends the browser to the error page without the exception's text, ends the visit so a second failure changes nothing, and does nothing once the browser has moved on; an optional failure sends nobody anywhere and leaves its message for ui.failed. A dashboard's load fails in place: no redirect, no recorded error, the visit not ended, the message with a reference left for ui.failed; an earlier page's recorded error survives the next opener. "Try again" only ever returns to a path of this site (one leading slash, no backslash), else to /. A load the user may not run, under a store like Redis, never runs: the refusal is in ui.failed, the warning names the handler and the user's id, nothing is left running |
| 1.778 | lex_page adds the opener and the error page exists | a page's on_load is the access load, any plain handler, then the opener carrying only the route; the error page shows the message and a way to try again; create_app loads rfds.toast before it adds a page, so it is never first imported in the middle of an event; lex_page refuses a route whose dynamic argument is named like a ui.State helper, and a load declared with arguments; create_app, once the project's pages are declared, refuses any page route with such an argument |

### Batch 1ck — lex.ui clears runs whose process is gone ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.779 – 1.780 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/runs.py` (`sweep`, `mark_finished`, `alive`, `heartbeat`), `lex/lex_app/reflex/ui/state.py` (`ui_open`) |
| Test file | `lex/test_project/tests/init/test_1ck_ui_stale_runs.py` |
| Test classes | `TestCluster01ck_StaleRuns` (1.779–1.780) |
| Fixtures | `_FakeRedis`, a stand-in that can error, hang or fail a set; the process record patched per test |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.779 | A sweep clears runs of a process that is gone | the dead process's run is cleared with its progress; this process's run stays; a process another worker reports alive keeps its run; an argument containing "|" is cleared completely; a dead process is asked about once however many runs it had; two runs under one key and argument keep their process record until the last finishes, and a sweep clears them all; a liveness check that hangs ends the sweep within its budget and leaves the marks; opening a page sweeps |
| 1.780 | Liveness is local without Redis and a heartbeat with it | without Redis only this process is alive; with Redis a process is alive while its heartbeat key exists, and the heartbeat writes this process's key with an expiry; a Redis that errors, hangs or cannot be built leaves a process counted alive; a failed beat does not end the heartbeat; ending it deletes the key and closes the one client; a forked child gets its own id |

### Batch 1cl — @ui.custom and the lex.ui findings of lex reflex check ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.781 – 1.782 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`custom`), `lex/lex_app/reflex/ui/checks.py`, `lex/lex_app/reflex/scaffold.py` (`run_check`) |
| Test file | `lex/test_project/tests/init/test_1cl_ui_custom_and_check.py` |
| Test classes | `TestCluster01cl_CustomAndCheck` (1.781–1.782) |
| Fixtures | a temporary project file with a `ui.State`, a raw `@rx.event`, a `@ui.custom`, `requires=ui.signed_in`, guarded and unguarded manager use, and a plain `rx.State`; files with undecorated public methods and non-handlers, import aliases, a ui state inherited across files, a file that does not parse, reserved field and method names and calls to sibling handlers (own and inherited); a project with only warnings and notes for `run_check` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.781 | ui.custom is a marked Reflex handler | an async handler under @ui.custom on a ui.State is an ordinary Reflex event handler -- async allowed, background on request -- that carries the custom mark |
| 1.782 | The check fails on unmarked raw handlers and lists the rest | the raw handler is an error; the custom handler and the signed_in requirement are notes; the unguarded manager is a warning; the guarded one and the plain state raise nothing; the report returns 1, and run_check returns non-zero even when RFDS's own checks pass. Undecorated public methods of a ui.State are errors (helpers, properties, static and class methods and computed vars are not); handlers are recognised through import aliases; a state inheriting a ui state from another file is checked; a file that cannot be analysed is one error naming it; a field, computed var or method named like a body helper (user, api, params, can, state) is an error; self.<handler>() of the state's own or an inherited handler, from any method, is an error that says to use an underscore method; run_check returns 0 when only warnings and notes are found |

### Batch 1cm — The lex.ui namespace; templates and the reference example on the layer ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.783 – 1.785 |
| Type | U |
| Files covered | `lex/ui.py`, `lex/lex_app/reflex/scaffold.py` (the templates), `lex/lex_app/reflex/examples/_reflex_structure.py` |
| Test file | `lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py` |
| Test classes | `TestCluster01cm_Namespace` (1.783–1.785) |
| Fixtures | the three templates rendered into a temporary directory; the example's states run through `ui.testing` with `auth.Group` / `Permission` grants |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.783 | from lex import ui has every name | each public name is the implementation's own object, ui.ApiError is lex-app's API error, and ui.testing is the test helper module; nothing else is public beside the page names and the table and adoption names, which batches 1cu, 1cv and 1cy cover |
| 1.784 | The scaffold templates are written on the layer | none contains async, await, run_orm or lex_api (as a name or an attribute); every state with handlers is a ui.State; the object page and the overview use ui.State with @ui.load; neither the lex.ui check nor RFDS's strict lint reports an error or a warning on any of them; the object page shows its load's failure, else the loaded record, else a spinner; its load, started on mount, reports a refusal in place (in ui.failed, no redirect) |
| 1.785 | The reference example is written on the layer | outside RFDS's own async export hook it contains no async, await, run_orm or lex_api; every state with handlers is a ui.State; the lex.ui check reports no error and neither check a warning; the overview's load, started on mount, reports its refusal in place on `/` (in ui.failed, no redirect), with the export menu offered only while the load has not failed; a viewer who may not read the featured model is refused by the load (no redirect) and by the export; a viewer who may read some models sees the same counts on screen and in the export, only for those models; the record dashboard reads the model `?model=` names, refuses a missing or unknown one -- in ui.failed, with no redirect -- and shows failure, else the record, else a spinner; the edit page is a form submitted to one action, with no per-keystroke handler, and `save` writes the submitted values |

### Batch 1cn — lex.ui: a load says when it has finished; the page registry decides who owns the error page ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.786 – 1.787 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/runs.py` (loaded), `ui/state.py` (ui_loaded, ui_open), `ui/handlers.py` (load) |
| Test file | `lex/test_project/tests/init/test_1cn_ui_loaded.py` |
| Test classes | `TestCluster01cn_Loaded` (1.786–1.787) |
| Fixtures | a small page state with two loads, one registered as required and one as optional, driven through `ui.testing` |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.786 | A load says when it has finished | ui.loaded(handler) is a view value that is false until the load has finished and true afterwards; opening the page again puts it back to false before the load runs, so a revisit shows the skeleton and not the last visit's content |
| 1.787 | Where a load is registered decides whether it owns the error page | a section's load that refuses puts its message in ui_failed and is not marked loaded, and neither the error page nor a redirect follows: the page stays, the section reports in place |

### Batch 1co — lex.ui pages: parts, translated sentences, text / fact / status / message ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.788 – 1.790 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/parts.py`, `ui/pages/vocab.py` (text, fact, status, message) |
| Test file | `lex/test_project/tests/init/test_1co_ui_page_parts.py` |
| Test classes | `TestCluster01co_PageParts` (1.788–1.790) |
| Fixtures | parts built on a stand-in page and slot, with a state value and RFDS's translation and formatters |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.788 | The sentence is the message | each sentence reaches RFDS's translation as both the id and the English default, with its values; a state value is shown as it is, untranslated; a tone outside the five is refused when the part is declared |
| 1.789 | A value says what it is and who sees it | each kind goes through RFDS's own formatter for it; a whole number is shown without decimals and any other with two; money takes a currency; an unknown kind, and a currency without kind="money", are refused when the fact is declared. visible= takes a permission (RFDS's check, with the string a page's requires= gives), a list of them (all must hold), ui.signed_in (always shown), or a state value; anything else, and an empty list, is refused with what to write. No component of the vocabulary takes a styling argument |
| 1.790 | A slot names what it refuses and what to write | the error names the page and the slot, says what it got, and says what to write instead; a single part and a list are both accepted; nothing gives an empty list |

### Batch 1cp — lex.ui pages: links by page function; ui.link and ui.button ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.791 – 1.793 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/links.py`, `ui/pages/vocab.py` (link, button, menu_entry) |
| Test file | `lex/test_project/tests/init/test_1cp_ui_page_links.py` |
| Test classes | `TestCluster01cp_PageLinks` (1.791–1.793) |
| Fixtures | two stand-in pages, one with a route argument and a declared query parameter |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.791 | An address is built from the page and its parameters | fixed values give a plain address; state values give an expression that joins them into it (a number is turned into text); a missing or unknown parameter is an error naming the linking page, the target page, its route and what it takes; a function that is not a page is refused with what to pass instead; a table's row link is the route with its one argument replaced by the row's key |
| 1.792 | A link leads to a page, a record or an outside site | to= stays in the app; record= opens lex-app's own page of that record; href= takes only a full outside address and opens it as external; exactly one of the three is required; among actions the same link is drawn as a button |
| 1.793 | A button runs a handler or opens a page | a handler button calls the handler on click; a to= button goes to the page's address; both or neither is refused; tone="danger" gives RFDS's danger intent; the first action of a page is the primary one; a menu entry carries the translated sentence and the same click |

### Batch 1cq — lex.ui pages: sections with their own loading and failure; each, switch, custom_block ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.794 – 1.796 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (section, states), `ui/pages/vocab.py` (each, switch, custom_block) |
| Test file | `lex/test_project/tests/init/test_1cq_ui_page_sections.py` |
| Test classes | `TestCluster01cq_PageSections` (1.794–1.796) |
| Fixtures | sections, each, switch and custom_block built on a stand-in page and dashboard with a state value |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.794 | A section with a load shows its skeleton, then its content or its failure | without a load the section is its content; with one, the content is shown only once ui.loaded says so, before that the failure message when there is one, else a skeleton (a table's, when the section is one table); on a page nothing is mounted (the opener starts the load); on a dashboard the load starts when the section mounts; a section's title is a sentence and comes first. Facts that follow one another form one key-value list; a section inside a section, and empty= without a table, are refused |
| 1.795 | each repeats and switch chooses | each item is rendered through the part the function returns; a function that returns a raw component is refused with what to return; switch shows the case matching the value and nothing otherwise; a case that is not a lex component is refused |
| 1.796 | Anything else goes through the marked way out | directly a raw component is refused, with the page, the slot and what to write; through ui.custom_block with a reason it is accepted and built as it is, and an error names it by its public name; without a reason, or with something that is not a component, custom_block is refused. A section hands its empty= sentence to a copy of its table, never to the author's part |

### Batch 1cr — lex.ui pages: ui.table over a model; rows that open a page ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.797 – 1.798 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/tables.py` (table, column), `lex/lex_app/reflex/data.py` |
| Test file | `lex/test_project/tests/init/test_1cr_ui_page_tables.py` |
| Test classes | `TestCluster01cr_PageTables` (1.797–1.798) |
| Fixtures | a throwaway model and two stand-in pages, one with exactly one route argument |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.797 | ui.table(Model) is the server-paged table | it is RFDS's data table over lex-app's paged table state for that model, showing the named columns under the model's own field labels; something that is neither a model nor a @ui.rows handler is refused with what to pass |
| 1.798 | Rows open a page, an empty table says so, a column can be relabelled | each row links to the page's address with the row's key in place of the page's argument; the empty sentence is translated and shown by RFDS's empty state; the column carries the given label; open= a page without exactly one argument is refused |

### Batch 1cs — lex.ui pages: the record and list kinds ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.799 – 1.802 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (record, list_, Kind) |
| Test file | `lex/test_project/tests/init/test_1cs_ui_page_kinds.py` |
| Test classes | `TestCluster01cs_PageKinds` (1.799–1.802) |
| Fixtures | record and list pages built on stand-in pages with a state, a load and sections |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.799 | A record page places status, facts and sections and waits for its load | it is RFDS's object page: the title in the page header, the status and the facts in the header area, each section under its translated heading; the whole page sits behind the load's loaded check, with a skeleton and the failure in place before it; the kind reports its page load and its sections' loads separately; tabs=True with two sections uses RFDS's tab navigation |
| 1.800 | A record page refuses what does not belong | each is a PageError naming the page and the slot and saying what to write -- also two sections with one title, a section hidden by a state value, a blank title, a title that is not words, two badges, and an action called Save (one fact or one section given without brackets is a list of one); a limit of RFDS's own (five tables on one page) names the page and quotes RFDS's sentence |
| 1.801 | Actions are placed as the page header places them | the first action is the primary button -- unless it is dangerous, which is never primary; exactly one further action is a button; two or more further actions are entries of the "More actions" menu, with their translated sentences and their tone; a primary action with visible= stays primary; the author's part is not changed; something that is not a button or a link is refused |
| 1.802 | A list page is a title, a table and its actions | it is RFDS's list report with the translated title, the table and the primary action; a table slot holding anything but a ui.table is refused |

### Batch 1ct — lex.ui pages: @ui.page, @ui.dashboard, registration once the module is loaded ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.803 – 1.805 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/declare.py` (page, dashboard, finalize), `lex/lex_app/reflex/app.py` (create_app) |
| Test file | `lex/test_project/tests/init/test_1ct_ui_page_declare.py` |
| Test classes | `TestCluster01ct_PageDeclare` (1.803–1.805) |
| Fixtures | stand-in pages declared in a scratch registry, the navigation and RFDS's page registry saved and restored |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.803 | A page is declared without being called and may link forward | decorating calls neither function and returns it unchanged, with what a link needs (route, its arguments, its declared query parameters); registration builds each page in lex-app's chrome under its route, with the permission from requires= as RFDS's permission string and the opener last in its load events. A route that does not start with "/", a requires= that is not one permission, a params= given as one string, and a second function at a route are refused when the page is declared |
| 1.804 | Registration hands over the navigation entry and the loads | the navigation entry carries the kind's title; the kind's load is registered as the page's required load and the section's load as an optional one; registering twice adds nothing; a navigation page without a fixed title, a load= that is not a @ui.load handler, one load given twice, a page function that raises, a component refusing its arguments, something that is not a section in sections=, a function without return, and a function that does not return a kind, are each refused with the page named |
| 1.805 | A dashboard starts its loads on mount and create_app registers the pages | the dashboard returns the built page, a component, whose load starts when it mounts (a dashboard has no opener); it is not a link target; on a model it sits under @classmethod and receives the model; two of its loads on one state are refused, since each clears its state as it starts; create_app loads the project's module, then registers the declared pages, before it adds the home page |

### Batch 1cu — lex.ui pages: the namespace; a list and a record page end to end ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.806 – 1.807 |
| Type | I |
| Files covered | `lex/ui.py`, `lex/lex_app/reflex/ui/pages/` |
| Test file | `lex/test_project/tests/init/test_1cu_ui_pages_namespace.py` |
| Test classes | `TestCluster01cu_PagesNamespace` (1.806–1.807) |
| Fixtures | a throwaway model; a list page and a record page registered and opened through `ui.testing` by a user with the grant |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.806 | One import gives the page kinds and the vocabulary | each name exists and is listed in __all__ -- except ui.list, the page kind, which a star import must not put in place of Python's list; no component or kind takes a styling argument |
| 1.807 | A list and a record page build, register and load | both build without one raw component in the page functions; the list links its rows to the record page; opening the record page runs the page load and then the section's load, both end loaded, and the state holds what they read |

### Batch 1cv — lex.ui tables: @ui.rows, a handler that returns a table's rows; ui.address; ui.refresh ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.808 – 1.811 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/rows.py`, `ui/pages/links.py` (address), `ui/results.py` (refresh), `ui/testing.py` (rows) |
| Test file | `lex/test_project/tests/init/test_1cv_ui_rows_handler.py` |
| Test classes | `TestCluster01cv_RowsHandler` (1.808–1.811) |
| Fixtures | rows handlers on a throwaway state, read through `ui.testing.rows` as a user with and without a grant |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.808 | A rows handler is a plain function that tables read | a plain function with no argument, or with `search` alone, is a rows handler; a generator, an async function and any other argument are refused with what to write; the browser cannot send it as an event; `lex reflex check` accepts it |
| 1.809 | Rows are read as the user on a read only copy | the rows carry the user and the field; lex-app searches and filters a handler without `search`, and a handler with `search` gets the text; writing a field fails the read and leaves the field as it was; a dict is refused with what to return; a missing permission refuses the read (a PermissionError in a test); given the table, the rows come back as its browser gets them |
| 1.810 | An address is built in python from a page | the argument and the query value are encoded into the page's route; an empty key gives no address; a value the page does not take, a missing argument, and something that is not a page are refused |
| 1.811 | Refresh names rows handlers and becomes their tables events | the result is accepted beside ui.toast; it becomes the event that makes that table read again, and is itself that list of events for a raw Reflex handler; something that is not a rows handler is refused |

### Batch 1cw — lex.ui tables: a table over a @ui.rows handler, end to end ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.812 – 1.817 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/pages/rows_table.py`, `ui/pages/tables.py` (the rows table), `ui/handlers.py` (reading again), `ui/state.py` (`ui_open`) |
| Test file | `lex/test_project/tests/init/test_1cw_ui_rows_table.py` |
| Test classes | `TestCluster01cw_RowsTable` (1.812–1.817) |
| Fixtures | rows tables built on a record page and a list page, driven through Reflex's own event processor with `ui.testing.Harness` |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.812 | The browser gets one page of the fields the table uses | its state holds the rows with only the columns' fields, the currency field, the key and the link field; the e-mail address stays on the server; the plain string is no link; the table's state is reachable for later reads |
| 1.813 | Search filters and sort run on the server | a search finds a row by its tag; each filter keeps the matching rows; the filter choices are the column's values after "All"; the first sort of a money column is high to low; clearing the filters clears the dropdowns; a filter the browser sends on a field the table does not show is refused |
| 1.814 | Totals per currency and export of what is shown | there is one totals row per currency over all matching rows; the export has the columns' labels and each amount's currency, the matching rows only, and tags joined; each exported column keeps its own cell type |
| 1.815 | A handler of the state or ui refresh makes the table read again | after each the table shows the new rows without being asked; a table that is not on screen does not read |
| 1.816 | Opening the page again and a list pages search in the address | the opener tells the page's table to read its first page again (not a table that appears only after a load, which reads when it appears); the list page's table starts with the address's search, and typing a search writes it back |
| 1.817 | A broken rows handler breaks only its table | a refusal shows its own words in the table; a failure shows a message with a reference and never its detail; a filter of more than 50 values is the table's error too (the advice to search goes to the log, with the reference) |

### Batch 1cx — lex.ui tables: what a column and a table refuse; a model table narrowed with where= ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.818 – 1.820 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/tables.py` (column, table, the checks, the model table), `lex/lex_app/reflex/data.py` (the filter's choice) |
| Test file | `lex/test_project/tests/init/test_1cx_ui_table_checks.py` |
| Test classes | `TestCluster01cx_TableChecks` (1.818–1.820) |
| Fixtures | a throwaway state with a rows handler; Django's Group and User models |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.818 | A column says what it is and is checked when declared | each kind is accepted; a currency code and a currency field are told apart; tones go with status only; show= and kind= are not given together; a link field must be a field name; a wrong kind names the kinds |
| 1.819 | A table declared wrongly names the page the slot and what to write | each is a PageError naming the page and the slot, saying what it got and what to write |
| 1.820 | A model table is narrowed with where and searched through the api | the table's state receives the where= values when it appears (a state value included); the toolbar has a search box; a filter offers the field's choices after "All"; two tables narrowed differently never share a state; a column keeps its field's kind; the field a row button reads reaches the browser; building the same table twice finds one state class; its state takes only its where= keys and its own filters, while lex_table's own state still filters on any field; search covers a shown text column only |

### Batch 1cy — lex.ui adoption: a lex.ui part inside a raw page; the user's permissions in a body ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.821 – 1.822 |
| Type | I |
| Files covered | `lex/lex_app/reflex/ui/pages/declare.py` (embed), `lex/lex_app/reflex/ui/context.py` (`User.permissions`) |
| Test file | `lex/test_project/tests/init/test_1cy_ui_adoption.py` |
| Test classes | `TestCluster01cy_Adoption` (1.821–1.822) |
| Fixtures | a throwaway state with a rows handler and an action, run through `ui.testing` with two grants |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.821 | A lex ui table sits inside a raw page | the table is RFDS's data table over its generated state, inside the raw page; a whole page kind and something that is not a lex.ui part are refused with what to write; a mistake in the embedded part names it by the name given |
| 1.822 | A handler body reads the users permission list | both see the user's Keycloak permissions as Keycloak lists them; outside a handler body reading them is refused |

### Batch 1cz — lex.ui tables: a table that leaves the screen keeps none of its rows ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.823 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/rows_table.py` (`close`) |
| Test file | `lex/test_project/tests/init/test_1cz_ui_table_close.py` |
| Test classes | `TestCluster01cz_TableClose` (1.823) |
| Fixtures | the people table of batch 1cw, through `ui.testing.Harness` |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Found while moving the reviewed customer project's tables to lex.ui: `close` only marked the table hidden, so its last
page stayed in the tab's state. 1.815's hidden table now holds no row either.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.823 | A table that leaves the screen keeps none of its rows | once it has left, its state holds no row, no count, no totals and no filter choice; when it appears again it reads its rows afresh |

### Batch 1da — lex.ui links: a raw Reflex page as somewhere a link may lead ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.824 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/links.py` (`reflex_page`), `lex/ui.py` |
| Test file | `lex/test_project/tests/init/test_1da_ui_reflex_page.py` |
| Test classes | `TestCluster01da_ReflexPage` (1.824) |
| Fixtures | the rows handler of batch 1cw; two raw pages declared with `ui.reflex_page` |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Found while moving the reviewed customer project's tables to lex.ui: its pages are raw Reflex, and a converted table
could only link to `@ui.page` functions, so its rows lost their links. 1.783's namespace gains `reflex_page`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.824 | A raw Reflex page is somewhere a link may lead | the address is the route with its value encoded as one piece, and is the kind of address a table follows; the table's rows link by their key; the link and the button lead there; a missing or unknown value, and a route that is no route, are refused with what to write; an app without a declared page does not start, naming the route |

### Batch 1db — lex.ui's part of lex-app's app, and every page in lex-app's chrome ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.825 – 1.826 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/declare.py` (`install`), `lex/lex_app/reflex/app.py`, `lex/ui.py` |
| Test file | `lex/test_project/tests/init/test_1db_ui_install.py` |
| Test classes | `TestCluster01db_Install` (1.825–1.826) |
| Fixtures | `rx.page` and the app's `add_page` recorded |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

First built as a project-frame hook (`ui.chrome`, a public `ui.install`) for the reviewed customer project, then
withdrawn after an independent review (design note `2026-11-24-reflex-ui-project-chrome-design.md`); what stays is
`create_app`'s one internal step. 1.778 and 1.805 read `create_app`'s call of it.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.825 | Create app hands lex ui its pages in one step | the declared page is registered once; the error page is added after the user's grants; the jobs' clock is registered once; the routes are checked after the pages; create_app runs the step, and lex.ui offers no app or chrome hook |
| 1.826 | Every page is in lex apps chrome after the grants | a page and the error page are in lex-app's chrome; the page's load events are the grants, then the opener - nothing of the project's between them |

### Batch 1dc — lex.ui handlers declared on a state mixin are each state's own ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.827 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`own_handlers`), `lex/lex_app/reflex/ui/state.py`, `lex/lex_app/reflex/ui/rows.py` |
| Test file | `lex/test_project/tests/init/test_1dc_ui_mixin_handlers.py` |
| Test classes | `TestCluster01dc_MixinHandlers` (1.827) |
| Fixtures | a mixin with a job and an action, two states using it; `ui.testing.Harness` |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

Found while moving the reviewed customer project's handlers to lex.ui: an export shared by every section of a page
showed every section busy while one exported, and ignored a second section's export as a repeat.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.827 | A handler from a mixin is each states own | each state keeps the handler under its own name, so a view of one state's running export does not show the other's; the second export runs too instead of being ignored as a repeat; a handler written on the state itself keeps its name |

### Batch 1dd — lex.ui tables: a date column is filtered by a range of days ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.828 | A date column is filtered by a range of days | the kind column has a dropdown and the date column a date-range picker; a month keeps its first to last day (a datetime on the last evening included, an undated row never); an open start or end; the range and the dropdown together; clearing empties the picker; a range for a field that is no date filter and a date that is not a day change nothing |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/tables.py` (the toolbar, filters by column kind), `lex/lex_app/reflex/ui/pages/rows_table.py` (`set_dates`, matching) |
| Test file | `lex/test_project/tests/init/test_1dd_ui_table_filters.py` |
| Test classes | `TestCluster01dd_TableFilters` (1.828) |
| Fixtures | a rows handler with dates of every shape; `ui.testing.Harness` |
| Tests landed | **1 pass / 0 fail** |
| Status | ✅ Complete |

First built as `ui.filter(kind="dates" | "text")`; after an independent review the filter's control follows the
column's kind (no new name), and the "contains" filter was dropped: `search=True` already does it.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.828 | A date column is filtered by a range of days | the kind column has a dropdown and the date column a date-range picker; a month keeps its first to last day (a datetime on the last evening included, an undated row never); an open start or end; the range and the dropdown together; clearing empties the picker; a range for a field that is no date filter and a date that is not a day change nothing |

### Batch 1de — lex.ui handlers may hand on to another state's handler ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.829 | A handler hands on to another states handler | the other state's handlers are results like the toast, in the order written, and reach the browser as they are; a browser script, a redirect and a plain object are refused with the handler's name and where raw events go |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/results.py` (`split`, `to_events`) |
| Test file | `lex/test_project/tests/init/test_1de_ui_reflex_events.py` |
| Test classes | `TestCluster01de_ReflexEvents` (1.829) |
| Fixtures | a raw Reflex state with two handlers |
| Tests landed | **2 pass / 0 fail** (the second: a @ui.rows handler or the running state's own handler is refused when returned) |
| Status | ✅ Complete |

First built to accept any Reflex event; after an independent review only another state's handler is accepted, so raw
events stay in the custom region. 1.747's docstring follows.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.829 | A handler hands on to another states handler | the other state's handlers are results like the toast, in the order written, and reach the browser as they are; a browser script, a redirect and a plain object are refused with the handler's name and where raw events go |

### Batch 1df — lex.ui: a button that says why it cannot be pressed; a download the browser can place ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.830 – 1.831 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/vocab.py` (`button(disabled=)`), `lex/lex_app/reflex/ui/results.py` (`download`) |
| Test file | `lex/test_project/tests/init/test_1df_ui_button_disabled.py` |
| Test classes | `TestCluster01df_ButtonDisabled` (1.830–1.831) |
| Fixtures | a rows handler with a reason per row |
| Tests landed | **2 pass / 0 fail** |
| Status | ✅ Complete |

Found while converting the reviewed customer project: row actions disabled with a reason per row, downloads of PDFs and
workbooks, and flags that kept jobs from overlapping (and stuck when a job failed). 1.770's reserved names gain
`is_running`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.830 | A button says why it cannot be pressed | each row's button is blocked exactly when its row has a reason, stays focusable and shows it; a fixed reason is shown; a yes/no field and True are refused with what to give instead |
| 1.831 | A download tells the browser what the file is | a file built from data carries the media type its name says; a download by URL carries none |

### Batch 1dh — lex.ui forms: what was entered reaches the handler as Python values, or as a message at its field ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.833 – 1.836 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/forms.py` (`clean`, `check`, `register`), `lex/lex_app/reflex/ui/pages/vocab.py` (`shape`: date, year, bool, choice) |
| Test file | `lex/test_project/tests/init/test_1dh_ui_form_values.py` |
| Test classes | `TestCluster01dh_FormValues` (1.833–1.836) |
| Fixtures | neutral order, invoice and ticket states |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

Step 2 of the pages design: forms (`ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=`). What building settled is
section 6.14 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.833 | Each kind reaches the handler as its Python value | text stripped; number int or Decimal; money and percent Decimal; date; date and time in the app's zone; year int; box bool; choice text; empties are "", False or None; undeclared fields dropped |
| 1.834 | A wrong or missing value is a message for its field | required, not a number, NaN, infinity, not a date, not four digits, a choice outside its options (fixed or a state field) each have their sentence |
| 1.835 | A handler receives one set of fields | the same fields again are accepted; others are refused naming the handler and where the first set was declared |
| 1.836 | A fact shows the kinds a field takes | date formatted by RFDS, year without separator, bool as Yes/No, choice as it is; an unknown kind lists the kinds |

### Batch 1di — lex.ui forms: the handler runs once, with checked values; the page follows what it did ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.837 – 1.841 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/forms.py` (`receive`, `done`), `lex/lex_app/reflex/ui/handlers.py` (`action`, `job`), `lex/lex_app/reflex/ui/state.py`, `lex/lex_app/reflex/ui/testing.py` (`Result.invalid`) |
| Test file | `lex/test_project/tests/init/test_1di_ui_form_submit.py` |
| Test classes | `TestCluster01di_FormSubmit` (1.837–1.841) |
| Fixtures | neutral order, invoice and ticket states |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

Step 2 of the pages design: forms (`ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=`). What building settled is
section 6.14 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.837 | The values are checked before the handlers body runs | with problems the body does not run and each field has its message; filled in, it runs once with a Decimal and the result flag is set |
| 1.838 | A refusal and a failure are shown and no result appears | a refusal in its own words, a failure with a reference, neither sets the result; sending again takes the old result away, also when refused at a field |
| 1.839 | A job reports its progress and then its result | an empty year does not start the job; progress before the result; rows stay on the server; a refused job shows no result |
| 1.840 | A wizard sends one form and runs its handler once | an empty step stays with its message; a filled one moves on; Back; only the last step runs the body with every value; an earlier problem returns there |
| 1.841 | A new visit starts the form over | the opener clears messages, steps, results and an open dialog |

### Batch 1dj — lex.ui pages: the form kind -- fields, one submit, a result; a wizard; a tool ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.842 – 1.846 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/forms.py` (`field`, `step`, `form`), `lex/lex_app/reflex/ui/pages/kinds.py`, `lex/lex_app/reflex/ui/pages/declare.py` |
| Test file | `lex/test_project/tests/init/test_1dj_ui_form_page.py` |
| Test classes | `TestCluster01dj_FormPage` (1.842–1.846) |
| Fixtures | neutral order, invoice and ticket states |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

Step 2 of the pages design: forms (`ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=`). What building settled is
section 6.14 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.842 | A form page draws each field and sends the form once | RFDS's input per kind, labels, help, required mark, the server's message per field, dropdown or type-ahead, the submit button after the handler showing it running; one event: the submit |
| 1.843 | A tool is a form with a result | a job's progress and Stop; the result behind its flag, full width, with its table, section and download; one primary button |
| 1.844 | Steps make the same form a wizard | RFDS's stepper, one form with the other steps hidden, Continue then the handler's name, Back |
| 1.845 | A form waits for its load and is a page like any other | registered with its load; skeleton until loaded; a field starts from the state; a result section's load is a part load |
| 1.846 | A form declared wrongly says the page, the slot and what to write | twenty refusals read word for word: key twice, unknown kind, file, options misplaced or wrong, other state, currency, fields/steps, submit, values argument, a handler with other fields; a field in a section |

### Batch 1dk — lex.ui: a button that asks first -- confirm=, ask= -- and that shows its handler running ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.847 – 1.851 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/vocab.py` (`button(confirm=, ask=)`), `lex/lex_app/reflex/ui/pages/forms.py` (`dialog`), `lex/lex_app/reflex/ui/pages/parts.py` (`with_overlays`), `lex/lex_app/reflex/ui/state.py` (`ui_ask`, `ui_ask_close`) |
| Test file | `lex/test_project/tests/init/test_1dk_ui_button_confirm_ask.py` |
| Test classes | `TestCluster01dk_ButtonAsks` (1.847–1.851) |
| Fixtures | neutral order, invoice and ticket states |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

Step 2 of the pages design: forms (`ui.form`, `ui.field`, `ui.step`, `confirm=`, `ask=`). What building settled is
section 6.14 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.847 | Confirm asks before the handler runs | the button opens RFDS's confirmation beside the page; agreeing closes it, then runs the handler; vague questions refused |
| 1.848 | A dangerous button must ask | refused as a button, a menu entry and a row action, with what to write; one dialog per table, with the row's key |
| 1.849 | Ask opens a small dialog and hands its values to the handler | RFDS's dialog with RFDS's form; empty: stays open with the message; filled: closes and the body gets the key and the values; wrong handler or fields refused |
| 1.850 | A button shows its handler running | action and job buttons load while running; a row's button only for its key; a raw handler or a page link not |
| 1.851 | The dialog is opened and closed on the server | asking records the dialog and values; dismissing forgets them |

### Batch 1dl — lex.ui forms: a field where the user chooses several of a list of options ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.852 – 1.855 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/forms.py` (`KINDS`, `clean`, `check`), `lex/lex_app/reflex/ui/pages/forms.py` (`field`, `Field.check`, `Field.control`), `lex/lex_app/reflex/ui/pages/vocab.py` (`shape`: `choices`), `lex/lex_app/reflex/ui/testing.py` (`run` with a list among the values) |
| Test file | `lex/test_project/tests/init/test_1dl_ui_form_choices.py` |
| Test classes | `TestCluster01dl_FormChoices` (1.852–1.855) |
| Fixtures | a neutral funds state (regions, funds, orders) |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

`kind="choices"` on top of the forms of 1dh-1dk. What building settled is in section 6.14 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. Existing tests 1.836 and 1.846 follow: the list of
kinds gains `choices`, and the message for options on a field that is no choice names it.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.852 | Several choices reach the handler as a list of the options | a list of texts in the options' order, each once; one value alone, `[]`, `None`, `""`, `False`; required: at least one; no option, or not texts: refused; options from the state |
| 1.853 | The field is RFDS's input for several and sends nothing while picking | checkbox group up to 12 fixed options, multi-select above or from the state; label, help, required mark, the server's message; `value=` a list or a state value; one event: the submit; compiled with Reflex's page compiler; in a step and in ask= |
| 1.854 | A run hands the body the checked list | through Reflex's event processor and `ui.testing.run`: the list in option order; nothing ticked or no option is a message at the field and no run; a wizard's last step; a dialog's row key and list |
| 1.855 | Several choices declared wrongly, and shown | no options, non-text options, a start that is no list, options on another kind, the kinds listed; `ui.fact(kind="choices")` joined by commas |

### Batch 1dm — lex.ui: a job reports progress from a callback -- self.progress(fraction, message) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.856 – 1.859 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`job`, `_drive`, `reporter`), `lex/lex_app/reflex/ui/state.py` (`State.progress`), `lex/lex_app/reflex/ui/context.py` (`RESERVED`), `lex/lex_app/reflex/ui/checks.py` (reserved names) |
| Test file | `lex/test_project/tests/init/test_1dm_ui_job_progress_callback.py` |
| Test classes | `TestCluster01dm_JobProgressCallback` (1.856–1.859) |
| Fixtures | a neutral reports state, and a stand-in library that reports from its own worker thread |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

`self.progress` beside `yield ui.progress(...)`, for libraries that report through a callback. What building
settled is in the `@ui.job` part of section 6.4 of `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`.
`progress` became a reserved name, so the job state of 1ci renames its field `progress` to `step`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.856 | A callback on the library's thread shows the job's progress | each fraction and message reaches the browser in order, as a yield's; the callback ran on the library's thread; a report carries no other field; the job ends with its result; `self.progress` on the body's thread beside `yield ui.progress` |
| 1.857 | After Stop the next report raises the stop a yield would | Stop and leaving a `stop_on_leave` page: `GeneratorExit` in the calling thread; the library stops; the body's cleanup runs; changes since the last yield discarded; no error; nothing running |
| 1.858 | Elsewhere self.progress says it belongs in a job | an action, a load, a job that has ended: `RuntimeError` naming `self.progress` and `@ui.job`; nothing reported |
| 1.859 | progress is a name a ui.State reserves | a property the browser cannot call; a field, computed var or method named `progress` refused when the class is defined; the route argument `[progress]` refused; `lex reflex check` reports it |

### Batch 1dn — lex.ui forms: a text field of several lines -- ui.field(key, lines=6) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.860 – 1.862 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/forms.py` (`field(lines=)`, `Field.check`, `Field.control`), `lex/lex_app/reflex/ui/forms.py` (`clean`: text) |
| Test file | `lex/test_project/tests/init/test_1dn_ui_form_lines.py` |
| Test classes | `TestCluster01dn_FormLines` (1.860–1.862) |
| Fixtures | a neutral order-notes state (notes, pasted codes, a row's reason) |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

`lines=` on the forms of 1dh-1dl. What building settled is in section 6.14 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.860 | Lines makes the field RFDS's text area, and it sends nothing while typing | RFDS's textarea with that many rows, full width; label, help, required mark, the server's message; `value=` a text or a state value; without `lines=` still the one-line input; one event: the submit; compiled with Reflex's page compiler; in a step and in ask= |
| 1.861 | The handler gets the text with its line breaks, trimmed at the ends | through Reflex's event processor and `ui.testing.run`: the lines as typed, blank space at the ends gone; only blank lines on a required field is a message and no run; a pasted list splits into its lines in a wizard; a dialog's row key and text |
| 1.862 | lines= belongs to text and is a number of lines | number, date, yes/no, choice and choices refuse it, naming the page, the slot and the field; `kind="text"` takes it; 1, 0, a text, `True`, 2.5 refused when declared, saying to leave it out for one line |

### Batch 1do — lex.ui: a state field named like a route argument stops the app at start-up, not the frontend build ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.863 – 1.865 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/state.py` (`check_app_routes`, `check_route_fields`), `lex/lex_app/reflex/app.py` (`create_app` docstring) |
| Test file | `lex/test_project/tests/init/test_1do_ui_route_argument_fields.py` |
| Test classes | `TestCluster01do_RouteArgumentFields` (1.863–1.865) |
| Fixtures | a neutral regions state (lex.ui) and orders states (plain Reflex) |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

Reflex refuses a dynamic route argument named like a field or computed var of any state
(`reflex/state.py`, `BaseState._check_overwritten_dynamic_args`, called from `setup_dynamic_args` when `App.add_page`
commits a page -- for `@rx.page` pages in `App._apply_decorated_pages`, at compile). The start-up check now finds the
same collisions; see section 6.2 of `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`. The raw
state of 1de renames its field `key` to `opened`: with 1da's `/orders/[key]` in the same process it is exactly
such a collision.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.863 | A field named like a route argument stops the app with a sentence | a lex.ui field and computed var, a plain Reflex field, an inherited one; a page the app added, a declared one, an optional argument, a route without its leading slash: a `ValueError` naming the field, its state and module and the route, saying to rename the field; Reflex itself refuses each name |
| 1.864 | What Reflex lets through starts | an argument named like a backend field, an event handler, a static part of a path, or nothing any state has: the app starts; Reflex lets each through too |
| 1.865 | The route check runs before the pages are built | `ui.install` calls `check_app_routes`, which checks the state fields; a helper's name (`[user]`) is still refused first, with its own sentence |

### Batch 1ds — lex.ui forms: a choice searched on the server ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.900 – 1.903 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/forms.py` (`Expect.search`, `searched`, `search`, `check`, `clean`, `MATCHES`), `lex/lex_app/reflex/ui/pages/forms.py` (`field`, `Field.check`, `Field.expect`, `Field.control`), `lex/lex_app/reflex/ui/state.py` (`ui_search`, `ui_matches`, `_ui_offered`) |
| Test file | `lex/test_project/tests/init/test_1ds_ui_form_searched_choice.py` |
| Test classes | `TestCluster01ds_SearchedChoice` (1.900–1.903) |
| Fixtures | a neutral orders state: 120 customers (with an e-mail each), regions, rows without a label |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

`options=` a `@ui.rows` handler, on top of the forms of 1dh-1dl. What building settled is section 6.15 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.900 | A search offers a page of matches and nothing else of the rows | the rows handler gets the tidied text; at most `MATCHES`, each `{value, label}`; no other field in any update; without `search` the labels are matched, folded; rows without a label: logged with what to write, a reference at the field; an unknown field ignored |
| 1.901 | The server accepts only a value it offered to the tab | an offered id reaches the body as the row gave it; an existing but unoffered or a forged id refused at the field; required; an earlier search's value still accepted; nothing offered, nothing accepted |
| 1.902 | The field is RFDS's combobox searching on the server | server-search mode (`shouldFilter:false`) over `ui_matches`; typing and opening send `ui_search`; the id under the key, the label shown; compiled; in a step and ask=, the dialog hands the key and the id |
| 1.903 | A searched choice declared wrongly | several from a search, a start value, an action as options, one field searching two rows handlers: each names page, slot and what to write |

### Batch 1dt — lex.ui: a refusal shown above a form's submit button, with a title ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.904 – 1.907 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/results.py` (`Refuse`), `lex/lex_app/reflex/ui/handlers.py` (`_classify`, `_toast`, `_in_place`, `action`, `job`), `lex/lex_app/reflex/ui/forms.py` (`receive`, `refused`, `done`), `lex/lex_app/reflex/ui/pages/forms.py` (`Form._page`, `dialog`), `lex/lex_app/reflex/ui/state.py` (`ui_refused`), `lex/lex_app/reflex/ui/testing.py` (`fields`) |
| Test file | `lex/test_project/tests/init/test_1dt_ui_refuse_in_place.py` |
| Test classes | `TestCluster01dt_RefuseInPlace` (1.904–1.907) |
| Fixtures | a neutral exports state: a form, a job form, row buttons that ask |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

What building settled is section 6.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`; the failures
table of the action-state design (6.5) gains the form's row.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.904 | Outside a form a refusal is a toast headed by its title | `Refuse(title=)` kept; RFDS's warning toast with the title first and the sentence as its description; without a title the sentence alone; nothing kept in place; a title that is no text refused |
| 1.905 | In a form the refusal stays above the button until the form is sent again | `ui_refused[key]` title and sentence, no toast; cleared by the next send, also one with a field problem, by a success and by a new visit; API "not permitted" kept too; an unexpected error a toast, not kept; a job's refusal kept |
| 1.906 | The form shows it above its submit button | RFDS's inline message inside the form, before the action bar; warning tone, `role="alert"`, the title line; compiled; a wizard too |
| 1.907 | A dialog that asked stays open with the refusal | the dialog's inline message above its buttons; an action's dialog stays open with it, closes on success; a job's dialog closed at the start, its refusal a titled toast |

### Batch 1du — lex.ui: a toast that offers to open the result elsewhere ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.908 – 1.910 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/results.py` (`toast`, `Toast.link`, `to_events`), `lex/lex_app/reflex/ui/pages/vocab.py` (`Link.target`), `lex/lex_app/reflex/ui/testing.py` (`Result.toasts`) |
| Test file | `lex/test_project/tests/init/test_1du_ui_toast_link.py` |
| Test classes | `TestCluster01du_ToastLink` (1.908–1.910) |
| Fixtures | a neutral export state (a job and an action that end with a linked toast); a page stand-in |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

What building settled is section 6.15 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.908 | A toast link leads where a ui.link leads | a page with encoded values and a parameter, a raw Reflex page, lex-app's record page, an outside address; which leave the dashboards |
| 1.909 | The toast is RFDS's toast with its action | through `ui.testing.run`: the toast with its link; RFDS's toast with one action worded as the link, redirecting (outside as outside); 10 s with an action; none without |
| 1.910 | A toast link written wrongly is refused where it is written | not a ui.link, a button, `visible=`, a missing or unknown page value, an empty key: each says what to write |

### Batch 1dy — lex.ui tables: the name pinned and given room, a code kept narrow, the Columns menu, every row at once, totals that say what they sum ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.940 – 1.946 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/tables.py` (`column(kind="name"/"code", pin=)`, `table(page_size=, choose_columns=, totals=)`), `lex/lex_app/reflex/ui/pages/rows_table.py` (page size, totals of all rows, their label), `lex/lex_app/reflex/data.py` (`ChosenColumns`) |
| Test file | `lex/test_project/tests/init/test_1dy_ui_table_columns_rows.py` |
| Test classes | `TestCluster01dy_TableColumnsRows` (1.940–1.946) |
| Fixtures | a neutral customers state (name, code, region, amount in two currencies); Django's `Group` as a model table |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |

Table needs found while rewriting dashboards. What building settled is section 5.14 of
`docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.940 | The name stays in place and has room; a code stays narrow | `pin=True`: RFDS's `sticky="start"`; `kind="name"`: RFDS's entity kind at its wide field width; `kind="code"`: RFDS's identifier at its narrow width; plain columns unchanged; model fields keep label and kind |
| 1.941 | A pin written wrongly says what to write | not a bool; on another column than the first; no `width=` argument (rule 6.1.4), the kinds documented |
| 1.942 | The user chooses, orders and sizes the columns | RFDS's column chooser in the toolbar; the table follows; the first column always shown and first; only the table's columns; widths 40-1,200 px; model tables too |
| 1.943 | `page_size=` sets the rows a page, `None` shows every row | 50 opens at 50 and is offered; default 25; `None` opens at 200 without the size picker, RFDS's pagination only past 200; model tables too; wrong sizes refused |
| 1.944 | Every row in one scroll, and past 200 the rest is reached | 150 rows on one page; of 250, 200 then 50 on the next page |
| 1.945 | A totals row says what it sums and can sum every row | "Total" with nothing narrowing; "Total of matching rows" / "Total of all rows" while a filter or search is on, per currency; mixing or another word refused |
| 1.946 | A table with all of it compiles | Reflex's page compiler: sticky column, column chooser, pagination |

### Batch 1dz — lex.ui pages: a section closed until the user opens it, saying how many rows it holds ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.947 – 1.949 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/kinds.py` (`section(collapsed=)`, `Section.titled`, the record's refusal), `lex/lex_app/reflex/ui/pages/forms.py` (a form's `result=` sections), `lex/lex_app/reflex/ui/pages/declare.py` (`ui.embed` of a closed section) |
| Test file | `lex/test_project/tests/init/test_1dz_ui_section_collapsed.py` |
| Test classes | `TestCluster01dz_SectionCollapsed` (1.947–1.949) |
| Fixtures | a neutral import tool (orders, fields read); Django's `Group` as a model table |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

What building settled is section 6.16 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.947 | A closed section opens in the browser and says how many rows it holds | RFDS's collapsible section, closed; title and the table's count once loaded, via RFDS's translation; open sections unchanged; no table: title alone; model tables; `ui.embed` |
| 1.948 | A closed section compiles | Reflex's page compiler: the disclosure and the counting title |
| 1.949 | A closed section where RFDS cannot close one is refused | on a record page, with `tabs=True` offered; `collapsed=` not a bool |

### Batch 1ea — lex.ui pages: a tool's result as tabs -- one section at a time, as on a record page ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.950 – 1.952 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/forms.py` (`form(tabs=)`, `Form._result`, `Form._tabs`), `lex/lex_app/reflex/ui/pages/kinds.py` (the record's `tabs=`, unchanged) |
| Test file | `lex/test_project/tests/init/test_1ea_ui_form_result_tabs.py` |
| Test classes | `TestCluster01ea_FormResultTabs` (1.950–1.952) |
| Fixtures | a neutral region report tool; Django's `Group` as a model table behind a permission |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

Tabs existed on record pages (`ui.record(tabs=True)`); the same argument now works where sections also are, a
form's `result=`. What building settled is section 6.17 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.950 | The result's sections are tabs, each title its tab | RFDS's tabs; titles as tabs, not repeated as headings; shared parts above; without `tabs=` as before; the record's `tabs=True` unchanged |
| 1.951 | Tabs compile and keep who may see a tab | Reflex's page compiler; RFDS's tab permission: a refused tab and its table not drawn; a section's load stays a part load |
| 1.952 | Tabs written wrongly say what to write | no result, one section, a part after a section, two of one title, a closed section, a state `visible=`, not a bool |

### Batch 1dv — lex.ui: a result held privately on the server -- ui.private ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.920 – 1.926 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/private.py`, `lex/lex_app/reflex/ui/workcopy.py` (`fields_of`, `snapshot`, `restore`, `reset`, `Draft`), `lex/lex_app/reflex/ui/state.py` (`_ui_private`, `ui_open`), `lex/lex_app/reflex/ui/context.py` (`user_id_of`), `lex/lex_app/reflex/ui/testing.py` (`GONE`), `lex/lex_app/reflex/ui/runs.py` (the clock's sweep) |
| Test file | `lex/test_project/tests/init/test_1dv_ui_private.py` |
| Test classes | `TestCluster01dv_Private` (1.920–1.926) |
| Fixtures | a neutral payroll state (a report not kept, a draft kept) |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |

`ui.private` for personal data in a tool's result. What building settled is section 6.11 of `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`. Test 1.783 follows: `ui.private` is a public name.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.920 | The value stays in memory, never in a pickle or the browser | an action, a job and a table's rows read it; every state pickled as Reflex's Redis and disk managers do, the state as the browser gets it, and every update hold no part of it; only a stamp; nothing kept is the empty value |
| 1.921 | Another user or another process never sees it | another user signed in on the tab is refused with the sentence and gets nothing; a process that never held it says the same; the page keeps what it had |
| 1.922 | It leaves after its idle time and its maximum age | reads within the idle time keep it; past the maximum age or the idle time it is gone; the lex.ui clock drops it from memory; LEX_UI_PRIVATE_VALUES bounds memory, least recently used first |
| 1.923 | A new visit drops it unless kept | the opener drops a value not kept (reading says so); keep=True stays; a load resets the one not kept and keeps the kept; a kept value that is gone is reset by the load: no error-page loop |
| 1.924 | What the user sees when it is gone | an action and a job are refused with the sentence (toast; ui.failed for a job); a table's message is the sentence; a failed action leaves it gone, and restores a kept value |
| 1.925 | ui.testing seeds and reads it like any field | fields= seeds it; result.fields reads it; ui.testing.GONE seeds a gone value and is what result.fields holds; GONE copies and pickles as itself |
| 1.926 | A private field declared wrongly says what to write | a public name, no type, a plain Reflex state, a limit of 0 or True, keep= not a bool; the empty value from the type; neither a backend var nor a var |

### Batch 1dw — lex.ui: a limit on how many runs of a job go at once -- @ui.job(limit=) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.927 – 1.929 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/handlers.py` (`job`, `_limit_message`), `lex/lex_app/reflex/ui/runs.py` (`take_place`, `give_place`) |
| Test file | `lex/test_project/tests/init/test_1dw_ui_job_limit.py` |
| Test classes | `TestCluster01dw_JobLimit` (1.927–1.929) |
| Fixtures | a neutral export state (an export held at a gate, a report form, a job that stops) |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

`limit=` on `@ui.job`. What building settled is section 6.12 of `docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.927 | Beyond the limit a press is refused at once | two run at once; the third is refused before the others end, with the sentence, as a toast and in ui.failed; its body never runs; afterwards it runs |
| 1.928 | A place is given back however the run ends | a failure, a body's refusal, a cancel and a form's wrong field each free the place; a run reporting through self.progress from a library's thread holds its place while it reports and gives it back when stopped; a refused press of a form's job leaves no field message and is a toast, not above the submit button; limit=1 has its own sentence |
| 1.929 | The limit is declared as a whole number | 0, True, 2.5 and "2" refused with what to write; the handler carries its limit, also into a mixin's copy |

### Batch 1dx — lex.ui forms: a result kept across visits -- ui.form(keep=True) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.930 – 1.935 |
| Type | U |
| Files covered | `lex/lex_app/reflex/ui/pages/forms.py` (`form` keep=, the failure under a kept form), `lex/lex_app/reflex/ui/forms.py` (`keep`, `kept`, `gone_message`), `lex/lex_app/reflex/ui/state.py` (`ui_open`) |
| Test file | `lex/test_project/tests/init/test_1dx_ui_form_keep.py` |
| Test classes | `TestCluster01dx_FormKeep` (1.930–1.935) |
| Fixtures | a neutral invoice report state (a job held at a gate, a kept private result, a load that keeps the customer) |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |

`keep=` on `ui.form`, after checking what `@ui.load(keep=)` already gave: the fields survived, the result flag did not. What building settled is section 6.18 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. Test 1.841 (a new visit starts a form over) still holds for a form that is not kept.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.930 | A kept form shows what its job found after the user was away | back while the job runs: running and its progress; back after: the result flag and the rows; the field starts from what was typed; a form not kept starts over |
| 1.931 | A kept result whose data is gone says so | within the private idle time the result shows; past it, no result and the sentence under the form; a private field not kept does not decide; a failure while away stays to be shown; sending again clears it |
| 1.932 | The kept form page compiles and shows its failure in place | RFDS's inline message bound to ui.failed for a kept form only; the field's start from the state; compiled with Reflex's page compiler; registered with its state; keep= True or False only |
| 1.933 | A kept form with a load keeps what the load keeps | after a revisit and the load: the kept private result and the field in the load's keep= survive; what the load does not keep is reset |
| 1.934 | A kept form keeps its refusal above its submit button | a ui.Refuse with a title stays above the button across a visit for a kept form and leaves for a form not kept; the line under a kept form does not repeat it; sending again clears both |
| 1.935 | A kept form with tabs and a searched choice | keep=True with tabs=True compiles with its search; back within the idle time the tab's table reads the kept private value; a customer offered before the visit is still accepted; past the idle time the form and the tab's table say it is gone |

### Batch 1ef — lex.ui calculation pages: the page kind, its form, a run started as the user -- ui.calculation ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.980 – 1.984 |
| Type | E |
| Files covered | `lex/lex_app/reflex/ui/pages/calculation.py` (`calculation`, its checks, the form view), `lex/lex_app/reflex/ui/calculations.py` (the page's state, `calculate`), `lex/lex_app/reflex/data.py` (`LexApi.calculate`, `LexApi.log`), `lex/lex_app/reflex/ui/context.py` (`self.api.calculate`, `self.api.log`) |
| Test file | `lex/test_project/tests/init/test_1ef_ui_calculation_start.py` |
| Test classes | `TestCluster01ef_CalculationStart` (1.980–1.984) |
| Fixtures | `UiRegionReport` (a calculation model with a gate and permission switches) and `UiRegionReportLine` in the cluster's models; `_ui_calc_aids.Session`: the harness with `self.api` = lex.ui's own API client answered by lex-app's real views through the signed-in `APIClient` |
| Tests landed | **5 pass / 0 fail** |
| Status | ✅ Complete |

The calculation page kind of section 6.19 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`, first piece: the kind, its checks against the model, its form and the start of a run. The handlers run in `ui.testing.Harness` against lex-app's real views.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.980 | The page is declared with form words and checked against the model | compiles to RFDS's form with a Calculate button; one state per page; lex-app's load; a model that is no calculation, an unknown field, a needed field left out, the status as an input, no fields: refused with what to write |
| 1.981 | The form creates the record as the user and starts its run in-process | the record's values; create view then Calculate request; the browser at ?run=<key>; SUCCESS with what calculate() computed; the log under the grid's run id |
| 1.982 | A user the model does not let create is refused above the button | no record; the refusal in ui_refused of the form's handler; the browser stays |
| 1.983 | With Celery on the run goes to a worker and the page does not wait | dispatched once, never in-process; the record IN_PROGRESS; the browser already at the run |
| 1.984 | self.api.calculate and self.api.log are the grid's Calculate and log | [] before a run; IN_PROGRESS at once; the run's Markdown log after; its newest line |

### Batch 1eg — lex.ui calculation pages: one run, followed until it ends -- its result, its error, its log ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.985 – 1.990 |
| Type | E |
| Files covered | `lex/lex_app/reflex/ui/pages/calculation.py` (the run's view, the result written with the model's fields), `lex/lex_app/reflex/ui/calculations.py` (`open`, `follow`, `error_words`, `newest_line`) |
| Test file | `lex/test_project/tests/init/test_1eg_ui_calculation_run.py` |
| Test classes | `TestCluster01eg_CalculationRun` (1.985–1.990) |
| Fixtures | the 1ef models and `_ui_calc_aids.Session`; lex-app's log cache left running (`e2e_unpatch`); a recorded worker whose report is lex-app's own task success callback |
| Tests landed | **6 pass / 0 fail** |
| Status | ✅ Complete |

Second piece of the calculation page (section 6.19 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`). Found while building: lex-app answers a record outside the reader's permission_read with 404, so the page says "There is no run N." for both.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.985 | The run's view shows the model's fields as the run's values | compiles; a field alone is a fact with the model's label; no descriptor reaches the page; badge, progress that follows, error, log; another model's field refused |
| 1.986 | A run in lex-app's process is followed until it ends | IN_PROGRESS then SUCCESS; the newest logged line or Running as progress; the run's values and log; the following ends by itself |
| 1.987 | A run on a worker is followed until the worker reports | still IN_PROGRESS and followed while the worker has not reported; SUCCESS and the worker's values after its success callback |
| 1.988 | A failed run shows its error in its own words | ERROR; the exception's text without the traceback; the failed run's log kept |
| 1.989 | A run the reader may not read is no run to them | nothing of the run in the state; the error page says There is no run N., as for a missing key |
| 1.990 | Leaving stops the watching, never the run | the follow job ends on a new visit; the run still ends in SUCCESS |

### Batch 1eh — lex.ui calculation pages: the past runs -- a model table, each row opening its run ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.991 – 1.993 |
| Type | E |
| Files covered | `lex/lex_app/reflex/ui/pages/calculation.py` (`history=`, the status column), `lex/lex_app/reflex/ui/pages/tables.py` (a model table's row link and order set by its page kind; a status column's words), `lex/lex_app/reflex/data.py` (`LexTableState.default_ordering`) |
| Test file | `lex/test_project/tests/init/test_1eh_ui_calculation_history.py` |
| Test classes | `TestCluster01eh_CalculationHistory` (1.991–1.993) |
| Fixtures | the 1ef models; `_ui_calc_aids.ViewsClient` for the table's rows read through lex-app's list view |
| Tests landed | **3 pass / 0 fail** |
| Status | ✅ Complete |

Third piece of the calculation page (section 6.19 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`).

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.991 | The past runs are a model table lex-app completes | compiled under Past runs; the status column added last, in the badge's words; each row a link to ?run=<key>; -id first; the author's search kept; the author's table unchanged |
| 1.992 | Past runs written wrongly say what to write | another model, open=, where=, not a table |
| 1.993 | The past runs are read as the user, newest first | three runs, newest first with their status, through lex-app's list view; none for a reader the model's permission_read refuses |

### Batch 1ei — lex.ui calculation pages: a file the run produced, and stopping a run ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.994 – 1.997 |
| Type | E |
| Files covered | `lex/lex_app/reflex/ui/pages/calculation.py` (a FileField alone in `result=`, Stop), `lex/lex_app/reflex/ui/calculations.py` (`download`, `stop`), `lex/lex_app/reflex/data.py` (`LexApi.file`, `LexApi._send`), `lex/lex_app/reflex/ui/context.py` (`self.api.file`) |
| Test file | `lex/test_project/tests/init/test_1ei_ui_calculation_file_stop.py` |
| Test classes | `TestCluster01ei_CalculationFileAndStop` (1.994–1.997) |
| Fixtures | the 1ef models, `UiRegionReport.with_file` writing a CSV into `report_file` (deleted after each test); lex-app's record of running calculations left running (`e2e_unpatch`); a recorded worker task and revoke |
| Tests landed | **4 pass / 0 fail** |
| Status | ✅ Complete |

Fourth piece of the calculation page (section 6.19 of `docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`). lex-app cancels only runs on a worker (`CalculationModel.cancel` answers 409 `sync_calculation_not_cancellable` otherwise); the page words that refusal.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.994 | A file the run produced is downloaded as the user | a button labelled as the model labels the field; the run's CSV and stored name through lex-app's download view; a forged field refused |
| 1.995 | Stop cancels a run on a worker and the page says so | the worker's task revoked; the record and the page CANCELLED; following ended; no failure |
| 1.996 | A run in lex-app's process cannot be stopped and the page says so | refused in words; the run still ends in SUCCESS; a later Stop: already ended |
| 1.997 | The whole page declared with ui.page compiles | registered with lex-app's load; compiled with the form, the past runs, the progress and Stop asking Stop this run? behind the edit permission |
