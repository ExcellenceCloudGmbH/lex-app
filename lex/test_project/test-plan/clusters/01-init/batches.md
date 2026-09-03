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

- **Scenarios:** 1.261-1.280
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

- **Scenarios:** 1.309-1.313
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
