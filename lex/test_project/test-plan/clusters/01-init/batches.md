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

### Batch 1ae — Streamlit auth proxy: iframe re-auth breakout (refused-to-connect fix) ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.274 – 1.279 |
| Type | U |
| Files covered | `lex/proxy.py` (`_unauthenticated_response`, `_is_iframe_document_request`) |
| Test file | `lex/test_project/tests/init/test_1z_proxy_iframe_breakout.py` |
| Test classes | `TestCluster01z_ProxyIframeBreakout` (1.274 iframe → 401 frame-breakout not IdP redirect, 1.275 `<frame>` also breaks out, 1.276 top-level HTML still redirects to `/auth/login`, 1.277 no `Sec-Fetch-*` keeps redirect, 1.278 non-HTML → 401 JSON, 1.279 `_is_iframe_document_request` case-insensitive + scoped) |
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

- **Scenarios:** 1.261-1.268
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

**Known gap:** the script's own logic (measure background → compare → reload once)
is covered by a hand-run DOM-double harness, not by CI, because this repository has
no JS runtime. Seven cases pass. Worth relocating to the frontend repo's vitest.

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

### Batch 1ad addendum 6 — following is now opt-in (scenario 1.274)

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

Matching the two surfaces is worth surrendering the theme menu only where someone
asked for it:

```bash
LEX_THEME_FOLLOW=1   # opt in; default is off
```

A page already pinned is freed by opening it once **without** `embed_options` in
the URL — with following off, nothing puts it back.

The eager-frames script (batch 1ai) is deliberately **not** gated on this. It
governs *when* component frames load and has nothing to do with the theme.
