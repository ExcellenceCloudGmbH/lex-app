## 1. Init — Project Bootstrap

**What it tests:** The two CLI commands a brand-new user runs on day one:

1. **`lex setup`** — scaffolds `.env`, `.run/` (PyCharm configs), and `migrations/` in a fresh project directory
2. **`lex Init`** — applies migrations, syncs Django models to Keycloak (as Resources + Scopes), registers default roles and policies, and loads `INITIAL_DATA` if configured

If either command is broken, a new customer cannot start using the framework at all. This is the front door.

**Why first:** Every other cluster depends on a running database, a synced Keycloak realm, and loaded seed data. Without `lex setup` and `lex Init`, nothing else works.

**Fixtures needed:**
- A minimal test project directory (temp dir) with `lex_config.py`, `app.py`, and a sample model
- Mock Keycloak admin API (real HTTP boundary)
- Test JSON seed file referenced by `INITIAL_DATA`
- `SeedableItem` — simple `LexModel` used as the target of seed data

### 1a. `lex setup` — scaffolding

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.1 | Fresh directory | `.env`, `.run/`, `migrations/__init__.py` created |
| 1.2 | Existing `.env` preserved | Re-running `setup` does not overwrite user's `.env` |
| 1.3 | `.run/` configs regenerated | PyCharm run configurations (`Init`, `Start`, `Streamlit`) written with correct paths |
| 1.4 | Missing project root | Clear error message, no partial scaffolding |
| 1.5 | `find_project_root` resolves correctly | Walks up to find `lex_config.py` or uses cwd |

### 1b. `lex Init` — first-run initialization

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.6 | First run on empty DB | Migrations applied, tables created for all project models |
| 1.7 | Second run (idempotent) | No-op for migrations, Keycloak sync shows no drift, no errors |
| 1.8 | Adds a new model, runs again | New table created, new Keycloak resource registered with default scopes (`list`, `read`, `create`, `edit`, `delete`, `export`) |
| 1.9 | Renames a model, runs again | Keycloak resource renamed (not duplicated), old name removed |
| 1.10 | Deletes a model, runs again | Keycloak resource removed, excluded from sync list |
| 1.11 | Default roles registered | `admin`, `standard`, `view-only` roles created in Keycloak |
| 1.12 | Default scope → policy mapping | `create`/`delete` → admin only; `read`/`list` → all roles; `edit`/`export` → admin + standard |
| 1.13 | Keycloak unavailable (timeout) | Non-fatal error, clear message, local state consistent |
| 1.14 | Missing Keycloak env vars | Fails fast with actionable error naming the missing variable |
| 1.15 | Keycloak state file updated | `.keycloak_state.json` reflects current synced state |
| 1.16 | Excluded apps skipped | `legacy_data`, historical/metahistorical models, `AuditLog` not synced to Keycloak |

### 1c. `INITIAL_DATA` loading (part of `lex Init`)

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.17 | Seed data loads on empty database | Records created with correct field values |
| 1.18 | Seed data skips when data already exists | No duplicates, no errors, existing data untouched |
| 1.19 | Invalid seed data format | Clear error message, no partial load |
| 1.20 | Seed data with foreign key references | Related records resolved correctly |
| 1.21 | `lex_config.py` parses | `INITIAL_DATA` path and `PROJECT_GROUPS` read correctly |
| 1.22 | Missing seed file | Skipped gracefully with a log message, `Init` still succeeds |

### 1q. Migration file completeness gate ✅

**Gap:** A framework release must never rely on customer machines to generate
missing migration files during `lex Init`; that creates downstream duplicate
migration conflicts when the missing files are later committed in a follow-up
release.

**Scenario range:** 1.147 – 1.147. **Test file:** `lex/test_project/tests/init/test_1q_migration_files_complete.py`. **Type:** U. **Status:** ✅ Complete (Session 70 — June 2).

### 1s. Log-noise cleanup + lex-namespace debug control (EXC-1787) ✅

**Gap:** A customer running LEX App V2 locally (ticket EXC-1787) reported two
log-ergonomics problems. (1) urllib3's `InsecureRequestWarning` spams local logs
on every Keycloak request because TLS verification defaults off; the framework's
intent is clean logs by default, suppressed at startup with an opt-out
(`LEX_SUPPRESS_INSECURE_WARNING=False`). (2) There was no way to see the
framework's own `logger.debug(...)` without the third-party DEBUG firehose that
`LOG_LEVEL=DEBUG` pulls in; `LEX_LOG_LEVEL=DEBUG` must raise only the `lex.*`
namespace while root stays at INFO. (3) Local startup also emitted Python warnings
(e.g. Django's "Accessing the database during app initialization" RuntimeWarning);
a blanket `LEX_SUPPRESS_WARNINGS` gate (default on) quiets these, with an opt-out.

**Scenario range:** 1.159 – 1.168. **Test file:** `lex/test_project/tests/init/test_1s_log_cleanup_and_lex_debug.py`. **Type:** U. **Status:** ✅ Complete (Session 75 — June 8). Letter note: 1r is an in-flight WIP batch (lex_view embed helper), so this batch took the next free letter 1s.

### Batch 1t — `DISABLE_SERVER_SIDE_CURSORS` placement (production cursor crash)

`lex/lex_app/settings.py` declared `DISABLE_SERVER_SIDE_CURSORS = True` at **module level**, where Django ignores it — the flag is only read from the per-database config dict (`connection.settings_dict`). In deployed environments behind the `cloud-sql-proxy-...-pooling` transaction-pooling proxy, PostgreSQL server-side (named) cursors therefore stayed enabled, and every `.iterator()` query (permission filter backend, list views, exports) raised `psycopg2.OperationalError: cursor "_django_curs_..." does not exist` because the `DECLARE` and a later `FETCH` were routed to different backend connections. Deterministic per request → no reload recovered, and the calculation-log panel never loaded from the DB. **Fix:** loop over `DATABASES` after the default is resolved and set `DISABLE_SERVER_SIDE_CURSORS=True` on every PostgreSQL-engine alias, so `.iterator()` falls back to pooling-safe client-side reads.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.169 | Flag set in every Postgres DB config dict | each PostgreSQL alias carries `DISABLE_SERVER_SIDE_CURSORS: True` inside its config dict — the only place Django reads it; guards against the module-level regression |
| 1.170 | Live default connection honours the flag | `connections["default"].settings_dict` reports it True on PostgreSQL (Django defaults the key to False when absent, so the old placement surfaces here as False); engine-gated for local SQLite |

**Scenario range:** 1.169 – 1.170. **Test file:** `lex/test_project/tests/init/test_1t_disable_server_side_cursors.py`. **Type:** U. **Status:** ✅ Complete (Session 78 — June 9). Source: `lex/lex_app/settings.py`.

### Batch 1u — Fast ASGI health/readiness probes ✅

**Gap:** PR #615 added a lightweight ASGI health/readiness layer so Kubernetes
liveness can return `200` without touching Django/DB, while readiness only
returns `200` once the database is reachable. A regression here either restarts
healthy pods (`/health` stops being cheap/static) or sends WebSocket/API traffic
to a pod before it can serve (`/readiness` lies ready).

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.171 | Health/readiness path helpers are disjoint | `/health` and `/api/health` are fast liveness only; `/readiness` and `/api/readiness` are DB-aware readiness only |
| 1.172 | Fast health ASGI app drains request body and returns static payload | multi-message request body is consumed; response is `200` with `{"status": "Healthy :)"}` and no-store headers |
| 1.173 | Readiness reflects DB availability | patched DB-ready seam returns `200 {"status":"ready"}`; DB-unavailable returns `503 {"status":"not-ready","reason":"database-unavailable"}` |
| 1.174 | Top-level ASGI app short-circuits probe paths | `/health` and `/readiness` call their lightweight ASGI apps and do not invoke Django |
| 1.175 | Non-probe HTTP delegates to Django | arbitrary application paths bypass health shortcuts and reach `django_asgi_app` |

**Scenario range:** 1.171 – 1.175. **Test file:** `lex/test_project/tests/init/test_1u_fast_health_asgi.py`. **Type:** U. **Status:** ✅ Complete (Session 81 — June 18). Sources: `lex/lex_app/fast_health.py`, `lex/lex_app/asgi.py`.

### 1v. `TIME_ZONE`↔`USE_TZ` coupling — `django_celery_beat` DatabaseScheduler correctness

`django_celery_beat`'s `DatabaseScheduler` reads naive datetimes through
`celery.utils.time.maybe_make_aware`, which **hardcodes naive == UTC**
(`ModelEntry.is_due` and `clocked.__init__`). The framework runs two beat
schedules through it: the recovery sweep (an `IntervalSchedule`) and future
history edits (a one-off `ClockedSchedule` fired at `History.valid_from`, see
`bitemporal_signals._schedule_future_activation`). Under `USE_TZ=False` (the
deliberate GCP/default production setting) Django stores **naive Berlin
wall-clock**, so if `TIME_ZONE` were a non-UTC zone beat would misread every
stored timestamp by the UTC offset — the interval sweep never becomes due and
clocked activations fire 1–2h late (DST-dependent). `settings.py` therefore
pins `TIME_ZONE = "Europe/Berlin" if USE_TZ else "UTC"`: under `USE_TZ=False`
the naive frame Django writes **is** real UTC; under `USE_TZ=True` datetimes
are tz-aware so the display zone stays free. Decoupling the two silently breaks
every beat-driven feature on a `USE_TZ=False` deployment.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.179 | `USE_TZ=False` forces UTC | when `settings.USE_TZ` is False, `settings.TIME_ZONE == "UTC"` so beat's naive-as-UTC read is correct; under `USE_TZ=True` a display zone is still configured |
| 1.180 | Django's naive frame matches beat's assumption | under `USE_TZ=False`, `timezone.now()` is naive and within seconds of real UTC, so a stored timestamp round-trips through `maybe_make_aware` unchanged |
| 1.181 | Recovery `IntervalSchedule` is due in the live frame | replicating `ModelEntry.is_due` (`maybe_make_aware(last_run_at).astimezone(app.timezone)`): just-ran → not due with `next ≤ interval`; 3×-overdue → due (the original stuck-sweep symptom) |
| 1.182 | Future-edit `ClockedSchedule` fires at `valid_from` | `clocked(now+30s)` is ~30s away (not hours) and not due; a past target is due immediately — the exact path future history edits take under Celery |
| 1.183 | Non-UTC naive storage is misread by the offset (regression rationale) | the same instant stored naive-UTC round-trips exactly through beat's reader, while naive-Berlin is misread by ≥3600s — the skew the coupling eliminates |

**Scenario range:** 1.179 – 1.183. **Test file:** `lex/test_project/tests/init/test_1v_scheduler_tz_invariant.py`. **Type:** U. **Status:** ✅ Complete (Session 84 — June 26). Source: `lex/lex_app/settings.py` (USE_TZ↔TIME_ZONE coupling); guards the `django_celery_beat` `is_due` path it must keep correct.

---

### 1w. `LEX_TASK_RECOVERY_ENABLED` defaults OFF — stuck calc resets on restart

**What it tests:** the recovery master switch defaults to **off**, so the startup sweep (`_handle_calculation_model_reset`) stays in blind-abort mode and a stuck `IN_PROGRESS` calculation is reset to `ABORTED` on the next server restart when no recovery-supervisor pod is running (local dev, CI, un-provisioned deploys). The liveness-aware sweep otherwise skips rows a *tracked* recovery task owns — but that ownership record lingers in Redis across a restart, so without a running supervisor the row is orphaned `IN_PROGRESS` forever. The flag gates only the recovery registry/heartbeat/supervisor + the sweep skip-set; it never touches calculation dispatch, so a calculation that dispatches sub-calculations is unaffected either way.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.184 | Env unset ⟹ off | with `LEX_TASK_RECOVERY_ENABLED` absent, `settings.LEX_TASK_RECOVERY_ENABLED is False` — the startup sweep blind-aborts stuck rows on restart |
| 1.185 | Explicit `=true` opts in | `LEX_TASK_RECOVERY_ENABLED=true` ⟹ `True`, so a deployment running the supervisor pod keeps its dead-worker requeue behaviour |
| 1.186 | Explicit off + case-insensitive | `=false` ⟹ `False`; `=TRUE` ⟹ `True` (the `.lower() == "true"` parse is casing-tolerant) |

**Scenario range:** 1.184 – 1.186. **Test file:** `lex/test_project/tests/init/test_1w_recovery_default_deployment_target.py`. **Type:** U. **Status:** ✅ Complete (Session 90 — July 1). Source: `lex/lex_app/settings.py` (`LEX_TASK_RECOVERY_ENABLED` default `true` → `false`). Nested-dispatch untouched — 7j/7q/8ab all pass.

---

### 1y. IDE-aware setup run configurations

`lex setup` generates runnable project commands for the IDE that invoked it.
VS Code integrated terminals are identified from `TERM_PROGRAM`/`VSCODE_*`
markers; PyCharm and compatible JetBrains terminals are identified from their
host/terminal markers. No marker is universal, and inherited markers can
conflict, so ambiguous environments deliberately generate both `.run/*.run.xml`
and `.vscode/launch.json`.

The VS Code launch set runs `python -m lex` through `debugpy`, uses the workspace
root as `cwd`, loads `.env`, carries the command-specific environment, and maps
PyCharm's Celery worker-count macro to a VS Code `promptString`. Generated
entries are namespaced with `LEX:` and merged into existing JSON/JSONC so setup
does not remove user launch configurations.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 1.203 | VS Code marker | only `.vscode/launch.json` is newly generated and reported |
| 1.204 | PyCharm marker | only `.run/*.run.xml` is newly generated and reported |
| 1.205 | No IDE marker | both IDE formats are generated as the safe fallback |
| 1.206 | Conflicting IDE markers | detection does not guess; both formats are generated |
| 1.207 | Cross-IDE parity | all ten commands retain module, args, env, cwd, `.env`, terminal, and worker prompt semantics |
| 1.208 | Existing JSONC launch file | custom configurations and inputs survive the LEX merge |
| 1.209 | Repeated setup | launch JSON is byte-identical with no duplicate configurations or prompts |
| 1.210 | Standalone generator | auto-detection is reused and the console wrapper exits successfully |

**Scenario range:** 1.203 – 1.210. **Test file:** `lex/test_project/tests/init/test_1y_ide_run_configs.py`. **Type:** U. **Status:** ✅ Complete (July 23). Sources: `generate_pycharm_configs.py`, `lex/bin/lex.py`, `pyproject.toml`.

### 1z. Streamlit auth proxy — iframe re-auth breakout (refused-to-connect fix)

**What it tests:** the Streamlit dashboard is embedded in an `<iframe>`. When the auth proxy (`lex/proxy.py`) has no valid identity it must re-authenticate — but Keycloak's login page is served with `X-Frame-Options: SAMEORIGIN` / `Content-Security-Policy: frame-ancestors 'self'` and cannot render inside a frame, so redirecting the iframe to the IdP makes the browser show "auth.&lt;host&gt; refused to connect". The proxy detects a framed document load (`Sec-Fetch-Dest: iframe`/`frame`) and breaks out to a top-level login instead, while leaving the ordinary top-level redirect and the API 401 untouched.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.211 | iframe → breakout, not IdP redirect | an unauthenticated `Sec-Fetch-Dest: iframe` document load returns a 401 HTML breakout (auto top-nav + `postMessage` + `target="_top"` link to the absolute `/auth/login`), never a `RedirectResponse` into the IdP |
| 1.212 | `<frame>` breaks out too | `Sec-Fetch-Dest: frame` is treated the same as `iframe` — both are framed contexts |
| 1.213 | top-level HTML still redirects | `Sec-Fetch-Dest: document` with `Accept: text/html` still redirects to `/auth/login` (a top-level page can render the IdP) |
| 1.214 | missing `Sec-Fetch-*` keeps redirect | a client that omits `Sec-Fetch-*` falls back to the pre-existing redirect (breakout only fires on a positively-identified frame) |
| 1.215 | non-HTML → 401 JSON | an XHR/API request (`Accept: application/json`) gets `{"error": "Authentication required"}` at 401, not a redirect or HTML |
| 1.216 | detector is case-insensitive + scoped | `_is_iframe_document_request` matches `iframe`/`FRAME`; `document` and a missing header are not framed |

**Scenario range:** 1.211 – 1.216. **Test file:** `lex/test_project/tests/init/test_1z_proxy_iframe_breakout.py`. **Type:** U. **Status:** ✅ Complete (2026-07-21). Source: `lex/proxy.py` (`_unauthenticated_response`, `_is_iframe_document_request`). Follow-up (separate change): give the embedded `auth_token` path a refresh token so the proxy renews silently instead of forcing re-login at the 4h SSO cap.

---

### 1aa. Embedded Streamlit token renewal ✅

**What it tests:** that the embedded Streamlit auth flow can renew itself — the token endpoint publishes when to renew, and the proxy adopts a renewed token instead of keeping the older one.

**Why a regression matters:** silent. Without a published expiry the caller has nothing to schedule against; if the proxy keeps the older token, every renewal is discarded and the session still dies at the original expiry — no error anywhere, just a user sent back to the login page mid-work.

**Scenario range:** 1.217 – 1.222. **Test file:** `lex/test_project/tests/init/test_1aa_embedded_token_renewal.py`. **Type:** U. **Status:** ✅ 6 pass.

---

### 1ab. Ignored client-role self-cleanup (`client-admin` platform role, LEX-5) ✅

**What it tests:** `lex init` builds tenant authz from every role found on the Keycloak client; roles in `IGNORED_CLIENT_ROLES` get no `Policy - <role>`. The sync only ever ADDS policies via Keycloak's import endpoint — it never deletes an entry just because it's missing from the re-imported payload — so a policy minted by an older lex-app for a role that later became ignored (here: the new platform-internal `client-admin` role, replacing the abandoned `release-manager`) would persist forever without an explicit cleanup step.

**Why a regression matters:** a stale `Policy - client-admin` silently grants tenant-app authorization to a role that is meant to be platform-internal only — exactly the authorization leak LEX-5 exists to close.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.223 | ignore-set contents | `client-admin` is in `IGNORED_CLIENT_ROLES`; the superseded `release-manager` is not |
| 1.224 | stale policy removed from config | a pre-existing `Policy - client-admin` is dropped from `auth_config['policies']` |
| 1.225 | reference detached from permission | a permission's `config.applyPolicies` has `Policy - client-admin` removed, other applied policies survive in order |
| 1.226 | no-op when nothing stale | a config with no ignored-role policy is left byte-for-byte unchanged |
| 1.227 | live delete by id | a live `Policy - client-admin` found via `get_client_authz_policies` is deleted via `delete_client_authz_policy` using its id |
| 1.228 | no-op when nothing live | no matching live policy → `delete_client_authz_policy` is never called |
| 1.229 | missing-id fail-fast | a matching live policy record without an `id` raises `CommandError` (mirrors `delete_resources_individual`'s permission-id check) |

**Scenario range:** 1.223 – 1.229. **Test file:** `lex/test_project/tests/init/test_1ab_ignored_role_policy_cleanup.py`. **Type:** U. **Status:** ✅ 7 pass. Source: `lex/lex_app/management/commands/init.py` (`IGNORED_CLIENT_ROLES`, `strip_ignored_role_policies`, `delete_stale_ignored_role_policies`). Design: `local_wiki/projects/admin-role-separation-5/README.md`. The live-delete-after-import ordering (so Keycloak's referential-integrity check on the policy delete passes) is not verified against a live Keycloak instance.

### 1ac. Streamlit session survival across an idle period ✅

**What it tests:** that a Streamlit dashboard left open and idle keeps working. Streamlit reads request headers off the *WebSocket handshake* (`st.context.headers` resolves the session's client), so every credential the proxy injects is a snapshot taken once, when the socket opened — and the socket then stays open for hours. Renewal therefore has to come from a channel that is still live: the proxy's `/auth/token`, which owns the refresh token and remains the only component allowed to spend it.

**Why a regression matters:** it is what the user sees. Before this batch an idle tab died on the access token's own lifetime and reported "Authentication Error: Missing user information" — a message about identity headers that were in fact present, on a page that could not recover, because the next rerun re-read the same frozen headers and invalidated again.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.230 | `/auth/token` needs the internal secret | a valid session cookie alone gets a 403 — page script can `fetch` with credentials, so the HttpOnly cookie must not be enough to exfiltrate a bearer token |
| 1.231 | the pull returns a token valid *now* | the response carries the refreshed token and its expiry, not the stale stored copy |
| 1.232 | the refresh token is never handed out | absent from the response — two components rotating it is the race the pull channel exists to avoid |
| 1.233 | an unrenewable session is a 401 | the caller must distinguish "renewed" from "sign in again" |
| 1.234 | session outranks the `st_access` cookie (HTTP) | forwarded as session auth *with* the refresh token; the cookie is set on every login, so consulting it first classified a fresh login as unrenewable `jwt` |
| 1.235 | an explicit `auth_token` still wins | the embedded renewal handoff keeps its precedence over the session |
| 1.246 | the WS handshake carries the renewable credential | the one moment a long-lived connection is handed anything must hand it the session's refresh token |
| 1.236 | a stale handshake token never replaces a renewed one | adopting on inequality reverted every renewal on the next rerun |
| 1.237 | a genuinely newer handshake token is adopted | a reconnect or a re-sourced iframe is a legitimate renewal path |
| 1.238 | renewal pulls from the proxy | the refresh token in session state stays untouched while the proxy answers |
| 1.239 | fallback when no proxy answers | the refresh token is spent only then — no second writer to race |
| 1.240 | identity survives an unrenewable token | the user stays authenticated and identified, flagged only as needing renewal |
| 1.241 | permissions are kept when Keycloak fails | a failed UMA lookup must not silently demote the user to no access |
| 1.242 | the refresher runs for session auth too | "the proxy handles it" meant nobody did — the proxy only delivers at handshake time |
| 1.243 | the refresher stops with the session | closing a tab sets no session-state flag, so a flag-only refresher outlived every abandoned session |
| 1.244 | a transient failure stays invisible | a proxy restart must not put a re-auth notice in front of someone reading a chart |
| 1.245 | a persistent failure surfaces recovery | past the grace window the cause is SSO max lifetime or a revoked session; a successful renewal clears the stamp |

**Scenario range:** 1.230 – 1.246. **Test file:** `lex/test_project/tests/init/test_1ac_streamlit_session_survival.py`. **Type:** U. **Status:** ✅ 17 pass.

### 1ad. Streamlit's asset bundle served ungated, and session durability ✅

**What it tests:** that loading a Streamlit dashboard is fast, quiet, and survives its own credential. Streamlit's frontend is code-split — 1.61 ships 365 JS chunks and names 107 of them in eager `<link rel="modulepreload">` tags — and the proxy in front of it authenticated every request through one catch-all route whose deny ran before it looked at the path. The bundle is therefore served by the proxy itself and never gated: it is package content from the installed wheel, identical for every install, with no tenant data and no identity. Everything else on the same host is the opposite, so half these scenarios exist to hold that line.

**Why a regression matters:** it is all three reports customers filed. Gate the bundle again and a credential-less moment is a hundred 401s, a cold start four times heavier than it needs to be, and — the one they saw — `TypeError: Failed to fetch dynamically imported module`, because Vite reports an HTTP failure on a dynamic `import()` as a type error. Widen the public allowlist by one path instead, and a dashboard becomes readable by anyone who knows the URL.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.247 | a chunk needs no credential | a hashed JS chunk returns 200 with no cookie, no `auth_token`, no `Authorization` |
| 1.248 | cache headers cut both ways | content-addressed files are `immutable` for a year; `manifest.json` is `no-cache` — a stale manifest names dead hashes and causes the same TypeErrors from a different direction |
| 1.249 | the bundle is compressed | a gzip-accepting client gets `Content-Encoding: gzip`, and compression actually reduces the bytes |
| 1.250 | hashes cannot drift | the served directory is exactly `streamlit.file_util.get_static_dir()`, and holds the `index.html` that names the hashes |
| 1.251 | a missing bundle degrades | an unlocatable bundle adds no routes and raises nothing — a slow authenticated `/static` beats an app that will not boot |
| 1.252 | bootstrap endpoints are public | `/_stcore/health` and `/_stcore/host-config` reach the upstream with no credential (probes have no session; host-config is read before the WebSocket exists) |
| 1.253 | the public path asserts no identity | none of the `X-Streamlit-User-*` / `X-Forwarded-User` headers are attached, so nothing downstream can read an anonymous probe as a signed-in user |
| 1.254 | the boundary holds | `/`, `/media/**`, `/_stcore/upload_file` and an arbitrary app route all still 401 |
| 1.255 | the socket is not public | an unauthenticated upgrade to `/_stcore/stream` is closed, not accepted |
| 1.256 | the upstream client is pooled | four sequential requests share one client, not one each |
| 1.257 | `Content-Encoding` survives | an encoded upstream body is relayed still encoded, so the saving is not thrown away |
| 1.258 | every cookie survives | two upstream `Set-Cookie` headers both reach the client — losing Streamlit's XSRF cookie breaks the handshake |
| 1.259 | the referrer is not leaked | proxied responses carry `Referrer-Policy: no-referrer`; the document URL holds a JWT |
| 1.260 | a failed refresh keeps its keys | stale keys survive an unreachable Keycloak, and the retry is deferred — returning `None` used to reject every token in the cluster |
| 1.261 | cold cache plus failure | no keys reported, and nothing raised (a raise here would become a 500 instead of an actionable 401) |
| 1.262 | one fetch, not a hundred | 25 concurrent callers share a single blocking fetch |
| 1.263 | reading keys never fetches | a cold cache reads as empty rather than blocking the event loop |
| 1.264 | you come back where you were | `auth_callback` honours the stashed `next` instead of redirecting to `/` |
| 1.265 | a hostile `next` is refused | `//host`, `https://host`, `http://host`, `/\host`, `\\host`, a bare host and `javascript:` are all rejected; same-origin paths survive intact |
| 1.266 | the session is not trusted | an off-origin path already in the session still falls back to `/` — validating only on entry is how open redirects survive review |
| 1.267 | the breakout can recover | the iframe recovery page's sign-in link is absolute, `target="_top"`, and carries the view being recovered |
| 1.268 | stripping keeps the destination | removing `auth_token` preserves `model`, `pk` and the rest — dropping them would trade one bug for another |
| 1.269 | only documents are redirected | `document` and `iframe` count; `empty` and `script` do not, so an XHR is served rather than 303'd |
| 1.270 | the session key is derived, not demanded | `DJANGO_SECRET_KEY` yields a durable cookie key, and is not reused verbatim |
| 1.271 | no configuration refuses to start | explicit secret, derivable secret, neither, and the published default all boot; only the first two are durable |
| 1.272 | replicas need a shared store | `LEX_PROXY_REPLICAS>1` without Redis raises; with Redis it boots |
| 1.273 | an unusable cookie is refused | `SameSite=None` without `Secure` raises (browsers discard such cookies outright), and so does a typo |
| 1.274 | cross-site frames get a cookie | https defaults to `SameSite=None`; local http stays `lax` |
| 1.275 | the session window outlasts a login | `--server.disconnectedSessionTTL` is passed and exceeds Streamlit's 120s default |
| 1.276 | the CLI reports without blocking | the pre-flight warns about an undurable session key rather than refusing to start |

**Scenario range:** 1.247 – 1.302. **Test file:** `lex/test_project/tests/init/test_1ad_proxy_assets_and_session_durability.py`. **Type:** U. **Status:** ✅ 56 pass, 42 subtests. **27 of the first 30 fail against the pre-fix tree**; 1.254, 1.255 and 1.271 pass by design as guards. Measured and explicitly not a cause: JWT validation at 0.045 ms/request — 16 ms across all 365 chunks. Recorded **BUG-029** (a pre-existing cluster-1 flake) while running this batch.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.277 | the strip hands over every credential | the `auth_token` redirect re-issues `st_access`, so it never delivers fewer credentials than the response it replaces |
| 1.278 | a bodiless response yields nothing | a 304 relays with no body chunk — `b""` would make GZip attach a body to it |
| 1.279 | a mid-body failure is not a truncated 200 | the error propagates, so the browser sees a network error rather than a half-delivered chunk |
| 1.280 | a probe with a body is not a 500 | `Content-Length` is never forwarded; httpx recomputes it from the real body |
| 1.281 | the pooled client is per event loop | two successive loops get different clients |
| 1.282 | the single-flight lock is per event loop | contended acquisition on a second loop does not raise |
| 1.283 | a malformed integer setting does not stop the proxy | `LEX_PROXY_REPLICAS=auto` warns and defaults to 1 |
| 1.284 | the bundle follows `baseUrlPath` | the mount moves to `/app/static`, and is not also left at `/static` |
| 1.285 | the pre-flight mirrors the cookie rules | `SameSite=none` without `Secure`, and an invalid value, both fail on the main thread |
| 1.286 | the https test agrees on both sides | `HTTPS://host` is recognised as a deployment by the CLI, as it is by the proxy |

**Scenarios 1.277 – 1.286** were added after an adversarial review of the first pass. All ten defects they pin lived in the *new* code, and four would have reproduced one of the three original symptoms by a different route — which is the argument for reviewing a fix as hard as the bug.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.287 | renewal reaches the proxy without an iframe reload | a newer token POSTed to `/auth/adopt` is adopted, and the session outlives its bootstrap token |
| 1.288 | only the configured frontend may adopt | a foreign `Origin` and a missing one are both 403 |
| 1.289 | adoption still validates | an unverifiable token is 401, an absent one 400 |
| 1.290 | the preflight is answerable and scoped | 204 naming POST for the allowed origin, 403 for any other |
| 1.291 | the allowlist derives from the instance hostname | `DOMAIN_HOSTED` alone permits adoption, and never `*` |
| 1.292 | the localhost default is not promoted | `DOMAIN_HOSTED=localhost` trusts the shell's dev ports, not `https://localhost` |

**Scenarios 1.287 – 1.290** exist because the earlier fixes created the gap between them: freezing the iframe `src` protects the Streamlit session but removed the only channel a renewed token had. Verified before fixing — an embedded session 401s past its bootstrap token's expiry with a valid renewal sitting unused in the shell.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.293 | a closed pooled connection is retried | a request after the upstream's keep-alive expiry succeeds on a fresh connection |
| 1.294 | the pool expires before the upstream does | the proxy's keep-alive expiry undercuts uvicorn's 5s, and reaches the client |
| 1.295 | an unreachable upstream answers 502 | `RemoteProtocolError` yields 502 on both the public and the authenticated path |

**Scenarios 1.293 – 1.295** come from a production log rather than review: pooling connections is what made a stale one possible, and `RemoteProtocolError` escaped both existing handlers as an unhandled ASGI exception. `/media/...` is proxied, so the visible symptom was a document download that failed for no stated reason.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.296 | a failed request is logged at WARNING | a 404 asset and a 401 path both appear with method, path and status |
| 1.297 | a successful asset stays quiet | suppressed by default, logged under `LEX_PROXY_ACCESS_LOG_STATIC` |
| 1.298 | the log never carries the query string | `?auth_token=<jwt>` is recorded as `?<query>`, status preserved |
| 1.299 | logging can be turned off | `LEX_PROXY_ACCESS_LOG=false` silences it without changing the response |

**Scenarios 1.296 – 1.299** exist because the production log for the bug in 1.293–1.295 had **no access lines**, so an asset failure could not be told apart from an auth failure or a missing file without curling the live host afterwards.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.300 | Streamlit's control exceptions are not Exceptions | `StopException`/`RerunException` derive from `BaseException`, so `except Exception` misses them |
| 1.301 | the stop flag survives a rerun | a read interrupted by either is "do not stop", and raises nothing |
| 1.302 | a real stop flag is still obeyed | the guard swallows control exceptions, not the answer |

**Scenarios 1.300 – 1.302** close the last live cause of the original report: the token refresher died on a script rerun, and a dead one is only replaced on the *next* run — so a rerun followed by idleness left nothing renewing the token. Found in a production log, which was only readable because 1.296–1.299 added the access log.

### 1ap. `lex reflex` — the Reflex command, its ports, and its workers' Django ✅

**What it tests:** that a project runs its Reflex dashboards the way it runs Streamlit's — `lex reflex` (default `reflex run`) from the project root, an `rxconfig.py` written once, fixed ports beside Streamlit's (8502 frontend, 8503 backend) handed only to the run modes that accept them, hot reload on the project's own files, a "Reflex" run configuration from `lex setup` — and that Reflex's worker processes, which the CLI never sets Django up for, get the CLI's environment.

**Why a regression matters:** a port Reflex refuses (`--frontend-port` on a backend-only run) stops the server before it starts; a worker without the project's `.env` sets Django up against the wrong database, and nothing says so.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.348 | `lex reflex` alone | runs `reflex run`; the CLI skips its Django bootstrap |
| 1.349 | arguments pass through | Reflex receives exactly the arguments given, `--help` included; project-less commands write nothing |
| 1.350 | rxconfig.py | written once from the template, never overwritten; Reflex runs from the project root |
| 1.351 | hot reload | the project's own top-level entries, never `.web`, hidden entries, virtualenvs or caches — and never an empty value |
| 1.352 | development ports | missing ports supplied; one chosen by flag, environment or config wins |
| 1.353 | one port per mode | backend-only, frontend-only and prod/preview runs each get only the port they can use |
| 1.354 | run configuration | PyCharm and VS Code both get "Reflex", running `reflex run` with the project's `.env` |
| 1.355 | a worker's environment | `prepare_environment()` mirrors the CLI and never overrides what is set; `setup_django()` never runs twice |
| 1.356 | one `.env` parser | the CLI's parser, shared with the workers |

**Scenario range:** 1.348 – 1.356. **Test file:** `lex/test_project/tests/init/test_1ap_reflex_cli.py`. **Type:** U. **Status:** ✅ Complete (2026-09-28). Batch: [1ap](batches.md).

### 1aq. Reflex dashboards — Keycloak sign-in, the Django ORM, and the `?model=&pk=` dispatch ✅

**What it tests:** that a Reflex dashboard gets what a Streamlit dashboard gets. Sign-in against the project's own Keycloak through Reflex Enterprise's `AuthPlugin` (no proxy), configured from the settings a project already has; the Django ORM from event handlers, which run on an event loop where Django refuses synchronous queries; a Django request's connection handling around every event; and lex-app's URL contract — `?model=&pk=` a record's dashboard, `?model=` a table's, neither the project's own — with Streamlit's checks and messages.

**Why a regression matters:** the first ORM query in a handler raising `SynchronousOnlyOperation`, one dropped database connection failing every later event until a restart, or a dashboard framed by lex-app asking the user to sign in with the wrong client.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.357 | `lex_config()` | the app module, Keycloak sign-in bound to the Keycloak session (no `offline_access`), no ports, valid app name; importing it creates no state |
| 1.358 | provider settings | `LEX_KEYCLOAK_*`, then `OIDC_*`, then Lex App's Keycloak settings — never `KEYCLOAK_CLIENT_ID` |
| 1.359 | the proxy's settings | `OIDC_ISSUER`, `OIDC_VERIFY_SSL`, the "Login with Keycloak" label |
| 1.360 | `run_orm` / `@orm` | synchronous Django code runs from the loop, returns, and raises its own errors |
| 1.361 | connection handling per event | stale connections retired around every event, never inside a transaction |
| 1.362 | the URL contract | each query string resolves to its view, in Streamlit's order |
| 1.363 | from the router | `resolve` and `current_record` read the URL an event sees, through the ORM |
| 1.364 | embed and logout | `lex_embed` and `is_logout_enabled` as lex-app sends them |
| 1.365 | the dispatch page | overriding hooks compiled in, broken hooks and structure modules refused loudly |
| 1.366 | the compiled app | sign-in routes, no Reflex Enterprise pages, `/ui-error` (where a failed `@ui.load` sends the browser), guard before `resolve`, then the lex.ui opener as every lex page's load events end, connection middleware, every dashboard |
| 1.367 | registration | the `Reflex` report and sidebar entry only when enabled; discovery never imports the Reflex package twice |
| 1.368 | granian's uvloop | Django setup on uvloop carries on and patches nothing, and `async_to_sync` works after it (rewritten for BUG-046; see 1bo below) |
| 1.369 | `LexUser` | `username` and `display_name` from Keycloak's claims |

**Scenario range:** 1.357 – 1.369. **Test file:** `lex/test_project/tests/init/test_1aq_reflex_dashboards.py`. **Type:** U + I. **Status:** ✅ Complete (2026-09-28). Batch: [1aq](batches.md). Permissions in Reflex dashboards are cluster [4n](../04-permissions/cluster.md); the search exclusion of the `Reflex` report is [10d](../10-api_layer/cluster.md).

### 1ar. Reflex dashboards sign in by themselves — no click, and the Keycloak session carries over ✅

**What it tests:** that a Reflex dashboard never asks a visitor to click to sign in when Keycloak can answer. Reflex Enterprise's `/login` is a palette that waits for a click; lex-app's (`lex_login_page`) starts the sign-in itself — the plugin's own redirect at the top level, a silent `prompt=none` request in a frame — and shows the button only when a click is the one way left, without ever bouncing a visitor back and forth.

**Why a regression matters:** a user signed in to lex-app clicking "Login with Keycloak" on every dashboard, after every restart of the Reflex server, in every new tab; or, worse, a dashboard bouncing between itself and Keycloak for ever.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.370 | `/login` is lex-app's page | the plugin's `login_page` by import path; signing in shown, the button only under `login_needs_click`; the browser check on mount; a project's own page wins; the palette for any other providers |
| 1.371 | the top level | signed out: the plugin's own redirect to Keycloak, interactive, the page asked for kept; signed in: on to that page; React's repeat mount does nothing |
| 1.372 | never a loop | the button within the retry window, automatic after it and at once after a sign-out; a sign-in that cannot start leaves the button and the plugin's error |
| 1.373 | in a frame | `prompt=none` with the plugin's own client, callback, state and PKCE; the button once Keycloak has said it cannot answer silently |
| 1.374 | "sign in first" | Keycloak's silent-request errors, through the plugin's callback, bring up the button for the same page with no error; any other error is the plugin's to report |
| 1.375 | storing tokens | a stored sign-in ends the need for a click; a token cookie over 4096 bytes is reported once, naming Keycloak's "Full scope allowed" |

**Scenario range:** 1.370 – 1.375. **Test file:** `lex/test_project/tests/init/test_1ar_reflex_automatic_sign_in.py`. **Type:** U. **Status:** ✅ Complete (2026-09-28). Batch: [1ar](batches.md).

### 1as. Reflex dashboards — a Keycloak access token too large for its cookie is stored compressed ✅

**What it tests:** that a Reflex dashboard keeps a user signed in when Keycloak's access token is larger than a browser will keep in a cookie (4096 bytes). Reflex Enterprise keeps the session, and a user's tabs in step, through its cookies; lex-app stores such a token compressed and restores it where the plugin reads the cookie, byte for byte, so everything else runs as the plugin wrote it.

**Why a regression matters:** with the cookie refused, a restart of the Reflex server or a new tab signs the user out, and one tab's sign-in signs the others out — in a realm with many clients, that is every user.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.376 | stored compressed, read as issued | a token too large for its cookie is stored compressed, small enough to keep; the plugin reads back the token as issued, and computes `at_hash` and the tab hash from it |
| 1.377 | only where needed | a token that fits is stored as the plugin would; one compressed already is not compressed again; compression that would enlarge a token is skipped; an opaque token is compressed whole |
| 1.378 | read defensively | damaged or forged compressed values — bad base64, truncated, too many parts, unknown form, inflating past the cap, not UTF-8 — are no token; the plugin's own values read as before |
| 1.379 | what the log says | compressing is logged once with both sizes; a token too large even compressed, or one compression cannot shrink, is one warning naming the cookie and "Full scope allowed" |
| 1.380 | the browser's cookie | a session with no tokens of its own — a new tab, a restart — restores the token from the cookie the browser sends |

**Scenario range:** 1.376 – 1.380. **Test file:** `lex/test_project/tests/init/test_1as_reflex_token_cookie_compression.py`. **Type:** U. **Status:** ✅ Complete (2026-09-28). Batch: [1as](batches.md).

### 1at. A Reflex development run behind one port — Reflex Enterprise's single-port proxy gets the Starlette app ✅

**What it tests:** that a Reflex development run serves its pages and its backend through the backend's one port, the way the platform runs dashboards in a pod. Reflex Enterprise's single-port mode proxies every path the backend does not own to Vite; lex-app hands that proxy the Starlette app it is written for, which Reflex 0.9.12 passes only as `starlette_app`.

**Why a regression matters:** the dashboard's URL answers `/ping` and 404s every page, and the instance's dashboards are gone.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.381 | repaired as Reflex loads rxconfig.py | the rxconfig.py `lex reflex` writes, and a hand-written one that keeps lex-app's sign-in, leave the proxy taking the Starlette app once Reflex has loaded them, and the configuration is still the project's |
| 1.382 | one port, both servers | run the way Reflex runs it, the backend's port answers the backend's routes itself and every other path, query string included, from the frontend server |
| 1.383 | once, and only where needed | repairing again changes nothing; a proxy that already takes the Starlette app, or names no `app` parameter, is left exactly as it is |

**Scenario range:** 1.381 – 1.383. **Test file:** `lex/test_project/tests/init/test_1at_reflex_single_port_proxy.py`. **Type:** U. **Status:** ✅ Complete (2026-10-02). Batch: [1at](batches.md).

### 1au. `lex --version` — the version /health reports, answered from anywhere ✅

**What it tests:** that `lex --version` prints the lex-app version a project runs — the one `/health` reports, read from `lex._version` rather than the package metadata — and answers from any directory without setting Django up.

**Why a regression matters:** "which version are you on?" is the first question support asks and the installation guide's first check. Without the option, `lex --version` answers `No such option`; read from the metadata, it disagrees with `/health` on every image built from a git ref.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.384 | the version /health reports | `lex --version` prints `lex, version <lex._version.__version__>` and exits 0, even when `lex/_version.py` was stamped after install and the package metadata says otherwise |
| 1.385 | answered from anywhere | in a directory that is no project, and that Django cannot load as an app, `lex --version` exits 0 with one version line |

**Scenario range:** 1.384 – 1.385. **Test file:** `lex/test_project/tests/init/test_1au_lex_version.py`. **Type:** U. **Status:** ✅ Complete (2026-10-03). Batch: [1au](batches.md).

---

### 1av. Startup hardening — nested project root, broken lex_config.py, cache tables, CORS origins ✅

**What it tests:** a Lex project in a subfolder of a repository runs with `python -m lex` and no wrapper variables; a broken `lex_config.py` stops startup instead of silently running with defaults; a fresh database no longer needs a manual `createcachetable`; `LEX_CORS_ALLOWED_ORIGINS` narrows CORS.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.386 | lex_config.py marks the root | `backend/` from itself and a subfolder; the git toplevel without the file |
| 1.387 | CLI in a nested project | fresh `import lex.bin.lex` in `backend/Inputs`: `PROJECT_ROOT=backend/`, settings `lex_app.settings` (a project `settings.py` is not loaded) |
| 1.388 | config fails to load | `ImproperlyConfigured` naming the error, from `ready()`'s check |
| 1.389 | wrong key types | each of the four keys (and legacy names) named in the error; a valid file loads; no file passes |
| 1.390 | after migrate | `post_migrate` creates `default_cache`/`oidc_cache`; a second run is harmless |
| 1.391 | before the initial-data load | `async_ready` has created the tables by the time it checks for data |
| 1.392 | `LEX_CORS_ALLOWED_ORIGINS` | unset: allow-all as before; set: only the listed origins |
| 1.405 | home boundary | `lex_config.py` in the home directory never wins; marker-less folders fall back to themselves |

**Scenario range:** 1.386 – 1.392, 1.405. **Test file:** `lex/test_project/tests/init/test_1av_startup_hardening.py`. **Type:** U (1.390–1.391 I). **Status:** ✅ Complete (2026-10-18). Batch: [1av](batches.md).

---

### 1aw. A plain pytest run is set up like `lex pytest` — PyCharm's test tree works ✅

**What it tests:** that a plain pytest run — PyCharm's runner, VS Code, a terminal — in a project with a `lex_test_config.yaml` gets the setup `lex pytest` does, from lex's pytest plugin, and that `lex pytest` runs on the same plugin.

**Why a regression matters:** PyCharm's test tree showed one collection error and no tests for every lex project, and projects copied `lex pytest`'s setup into their own `conftest.py` to work around it. If the plugin and `lex pytest` drift apart, the same suite passes in one and fails in the other.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.393 | the PyCharm way | a model-importing, database-writing test passes under `python -m pytest`, with lex's warning policy in force |
| 1.394 | inactive elsewhere | without a `lex_test_config.yaml` the plugin does nothing |
| 1.395 | the opt-out | `-p no:lex` switches it off |
| 1.396 | the entry point | `lex = "lex.tools.pytest_plugin"` in pyproject |
| 1.397 | collect-only needs no database | an unreachable database does not stop `--collect-only` |
| 1.398 | a database that cannot be set up | exit code 4 with the database's error, no test run |
| 1.399 | groups are markers | configured groups are markers; an undeclared marker exits 4 |
| 1.400 | invalid test config | exit code 4 with the loader's message |
| 1.401 | pytest-django alongside | refused with a message naming `-p no:django` |
| 1.402 | a copied conftest | whichever set up first, nothing is set up twice |
| 1.403 | one implementation | `lex pytest` and `lex pytest-groups` pass the plugin with `-p no:lex` |
| 1.404 | test run configurations | `Tests` and one per group, for PyCharm and VS Code |

**Scenario range:** 1.393 – 1.404. **Test file:** `lex/test_project/tests/init/test_1aw_pytest_plugin.py`. **Type:** U / I. **Status:** ✅ Complete (2026-10-15). Batch: [1aw](batches.md).

---

### 1ax. The Process model feature removed from the public surface ✅

**What it tests:** `Process` — the abstract model a project subclassed to describe a workflow — is gone, and every door into it is shut: the import, the admin site's registry, the registration branch, the reserved class name, and the `Process` node type in the model-structure payload. `HTMLReport`, the sibling feature that shares the same machinery, was deliberately kept, so each scenario checks it still works.

**Why a regression matters:** a feature that is only half removed is worse than either state. A surviving `registerProcess` collects classes nothing reads; a stale `Process` in `_RESERVED_CLASS_NAMES` keeps rejecting a project's own model for a framework class that no longer exists; a `Process` node type sends the frontend to an endpoint that is gone.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.406 | not importable | `lex.core.models.Process` raises `ModuleNotFoundError`, `Process` left `__all__` |
| 1.407 | no registry left | no `processes` / `registerProcess` / `get_process_structure_func` / `_register_process_model`; the HTMLReport equivalents survive |
| 1.408 | the name is the project's again | `class Process(LexModel)` validates; `LexModel`, `CalculationModel`, `HTMLReport` and `Historical*` still raise |
| 1.409 | no `Process` node type | the structure payload types nodes `Model` / `HTMLReport` / `Folder` only |

**Scenario range:** 1.406 – 1.409. **Test file:** `lex/test_project/tests/init/test_1ax_process_feature_removed.py`. **Type:** I. **Status:** ✅ Complete (2026-10-22). Batch: [1ax](batches.md).

### 1bo. A project's startup leaves asyncio unpatched and `async_to_sync` usable ✅

**What it tests:** that setting up a downstream Lex project changes nothing about asyncio. Every process the project runs does this setup: the web server, a Celery worker, a management command, Streamlit. `asyncio.run` stays the standard library's, and synchronous code can call into async code with `async_to_sync` as often as it needs to, from any thread.

**Why a regression matters:** `LexAppConfig.ready()` used to call `nest_asyncio.apply()`, and nest_asyncio's `asyncio.run` never closes its loop. The first `async_to_sync` with thread-sensitive work (Django's async ORM, the Reflex ORM bridge) left that open loop in the calling thread, and the second call blocked on it forever (BUG-046). A Celery task, a command or a dashboard would hang on its second call, without any error.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.665 | set up with no running loop | after `django.setup()` of a downstream project in a fresh interpreter, `asyncio.run` is the standard library's, and two `async_to_sync` calls with thread-sensitive work both return, on the calling thread |
| 1.666 | set up inside the server's loop | the same when `lex_app.asgi` is imported inside a running asyncio loop, as uvicorn does |

1.368 (1aq, granian's uvloop) was rewritten to the same contract: setup on uvloop carries on and patches nothing.

**Scenario range:** 1.665 – 1.666. **Test file:** `lex/test_project/tests/init/test_1bo_bootstrap_leaves_asyncio_unpatched.py`. **Type:** U (fresh interpreter, no database). **Status:** ✅ Complete (2026-11-06). Batch: [1bo](batches.md). The startup reset's side of the same bug is cluster [8d](../08-celery_async/cluster.md).
### 1bc. Deployed secret key, session lifetime and PKCE ✅

**What it tests:** the sign-in settings a deployed instance runs with. A pod with `DEPLOYMENT_ENVIRONMENT` set refuses to start without its own `DJANGO_SECRET_KEY`, or on the key published in this package. The Django session cookie lives `LEX_SESSION_COOKIE_AGE_DAYS` (default 31) days and the OIDC library's session clock 30 days, so Django no longer ends a sign-in before Keycloak's 30-day idle window. Both relying parties on the shared Keycloak client -- Django's `/oidc/authenticate` and the dashboard proxy's `/auth/login` -- send a PKCE S256 challenge and keep the verifier server-side.

**Why a regression matters:** a pod on the public key signs with a value anyone can read; a Django session shorter than Keycloak's logs people out early; one relying party without PKCE blocks requiring PKCE on the client.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.481 | deployed, no key | `ImproperlyConfigured` naming `DJANGO_SECRET_KEY` |
| 1.482 | deployed, published key | `ImproperlyConfigured` |
| 1.483 | private key, local run | the deployed pod signs with its key; a local run keeps the development key |
| 1.484 | lifetime defaults | cookie 31 d, library clock 30 d (inside the cookie), RS256 only, PKCE with the client secret |
| 1.485 | lifetime from the environment | 45 → 45 d / 30 d; 10 → 10 d / 9 d; `abc` and `1` refuse to start |
| 1.486 | the library accepts the lifetimes | `RefreshSessionMiddleware` constructs with the defaults and the 2-day minimum |
| 1.487 | sign-in redirect | `code_challenge_method=S256`, challenge = S256(session verifier), no verifier in the URL |
| 1.488 | code exchange | the token request carries `code_verifier` and the client secret; the user is signed in and sent to `next` |
| 1.489 | proxy sign-in | `/auth/login` redirects with an S256 challenge and no verifier |

**Scenario range:** 1.481 – 1.489. **Test file:** `lex/test_project/tests/init/test_1bc_auth_settings_hardening.py`. **Type:** U (1.481–1.486, 1.489), I (1.487–1.488). **Status:** ✅ Complete (2026-11-06). Batch: [1bc](batches.md).

### 1bd. lex-app 2.5 on django-oauth2-codeflow 3.0: settings, routes and bootstrap ✅

**What it tests:** the settings and bootstrap half of the long-lived sessions plan (P4). The OIDC middlewares run in the library's order; server-to-server Keycloak traffic can use `KEYCLOAK_INTERNAL_URL` while tokens keep the public issuer; TLS to Keycloak is verified on deployed instances; CORS is strict by default (security flag `CORS`); `LEX_INSTANCE_HOSTS` is parsed in one place; the library's sign-out events reach stdout; the contract endpoints are routed and public; `lex init` keeps the client's sign-in hardening and writes no migration into the library.

**Why a regression matters:** wrong order or issuer → spurious sign-outs; permissive CORS → any site acts as the user; invisible sign-out events → no alert.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.490 | middleware order | auth middlewares in the plan's order, no 2.x aliases, library E001 passes |
| 1.491 | internal discovery, public issuer | discovery may be internal, `OIDC_OP_ISSUER` stays public; PKCE + `client_secret_post` |
| 1.492 | TLS and encryption keys | `KEYCLOAK_VERIFY_SSL` on when deployed; `OIDC_TOKEN_ENCRYPTION_KEYS` parsed |
| 1.493 | strict CORS | instance origins only, no `*`, no localhost when deployed; `LEX_SEC_CORS=false` opt-out |
| 1.494 | CORS on the wire | foreign origin gets no CORS grant; allowed origin gets credentials |
| 1.495 | redirect targets | sign-in follows `next` only to instance hosts |
| 1.496 | `LEX_INSTANCE_HOSTS` | JSON, fallback, bad input |
| 1.497 | sign-out events visible | `event=oidc.session.*` on stdout at INFO |
| 1.498 | contract routes | `api/auth/session`, `oidc/auth-complete` routed, public |
| 1.499 | `lex init` keeps hardening | `attributes` merged, `frontchannelLogout` restored |
| 1.500 | no library migration drift | nothing planned for `oauth2_authcodeflow` |

**Scenario range:** 1.490 – 1.500. **Test file:** `lex/test_project/tests/init/test_1bd_auth_core_settings.py`. **Type:** U (1.490–1.493, 1.496–1.497, 1.499–1.500), I (1.494–1.495, 1.498). **Status:** ✅ Complete (2026-11-06). Batch: [1bd](batches.md).

### 1be. Streamlit dashboards sign in through the app (the Django-anchored hand-off) ✅

**What it tests:** plan "long-lived sessions" §6.3. A dashboard holds no Keycloak token of its own: with no `__Host-lex_dash` handle, its proxy sends the frame to the same-site app host's `oidc/handoff`; the app mints a single-use code for the browser's sign-in (or answers `login_required` -- never Keycloak in a frame); the proxy redeems it in-process into a `DashboardBinding` on the user's `OIDCSession`; every request and socket then gets a fresh token of that sign-in from the broker, and only that token and identity headers reach Streamlit. `LEX_STREAMLIT_AUTH` (legacy / dual / handoff) switches it, with legacy untouched.

**Why a regression matters:** dashboards that log out every five minutes, refresh tokens in customer code, Keycloak pages that cannot render in a frame, redirect loops when cookies are blocked, or a user who can name someone else's identity to Streamlit.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.501 | a framed dashboard hands off | 303 to `<app>/oidc/handoff?aud=streamlit&return&state`; nonce cookie `__Host-lex_dash_hs` with the plan's attributes; `lex_hs=1` on the return URL |
| 1.502 | the app host the user signed in on | framed: the frontend's own listed origin (`lex_app_origin`; Django's session cookie is host-only); standalone: the listed app host same-site with the dashboard; unlisted `lex_app_origin` ignored; an unlisted Host is a 400, never a redirect |
| 1.503 | the round trip | through the real `oidc/handoff`: handle cookie (HttpOnly; Secure; SameSite=None; Partitioned; Path=/; 30 d), nonce cleared, back on the requested view, binding on the user's sign-in |
| 1.504 | only token + identity upstream | marker stripped first; `Authorization` = the sign-in's current token; no refresh/id/other token headers |
| 1.505 | state binding, single use | foreign state never redeemed; a replay is refused and revokes the first binding |
| 1.506 | 60-second code | an older code signs nothing in; the frame gets the `required` page |
| 1.507 | ended sign-in | binding revoked at its next use; document → fresh hand-off, handle cleared; XHR → 401 `session_expired` |
| 1.508 | Keycloak down | still-valid token kept; then identity only + `X-Lex-Auth-Degraded`; never a 401; `/auth/token` 503 |
| 1.509 | WebSockets | token + identity only; no handle / ended → accept, then close 4401 |
| 1.510 | `/auth/token` | 200 for the frozen cookie (either name); 403 without the secret; 401 only without a handle or once ended |
| 1.511 | logout, sign_out | binding revoked; signed-out page; `sign_out` → app `oidc/total_logout`, foreign `rd` replaced |
| 1.512 | blocked cookies | a code without its nonce is never redeemed; second attempt → storage-access / new-tab page, no loop |
| 1.513 | `login_required` | v2 `required` message posted to the listed `lex_app_origin` only; never `*`, no token |
| 1.514 | `/auth/required` | the Streamlit recovery target posts `session_ended`; 404 in legacy |
| 1.515 | concurrent hand-offs | two frames both complete; at most five nonces kept |
| 1.516 | daily cookie re-set | no churn the same day; re-set for 30 days after a day; the handle never changes |
| 1.517 | dual mode | handle wins over a legacy session; legacy still works; no credential → hand-off, not `/auth/login` |
| 1.518 | handoff mode | `auth_token` ignored and dropped; `/auth/adopt` no-op 200; `/auth/login` → hand-off; `/auth/callback` 404 |
| 1.519 | legacy mode | `/auth/handoff`, `/auth/required` 404; a handle cookie is ignored |
| 1.520 | proxy-owned headers (BUG-037) | client copies of identity/token headers never reach Streamlit, HTTP and WS, legacy and hand-off |
| 1.521 | the flag at the proxy | unset → legacy; case-insensitive; a typo refuses to start; handoff keeps no token store |
| 1.522 | `lex streamlit` pre-flight | typo, missing app host, missing dashboard host refused; replicas without Redis fine in handoff, refused in dual; http warns |
| 1.523 | Streamlit: 401 vs 503 | 401 → recovery at once; 503 / unreachable → never; legacy keeps its grace window |
| 1.524 | Streamlit recovery | the dashboard document goes to `/auth/required` (embedded only), navigated by itself -- a sandboxed component frame may not navigate its parent (run under node against that rule); legacy message unchanged |
| 1.525 | no refresh token in Streamlit | header sync takes the access token only; hand-off sign-out → `/oauth2/sign_out` |
| 1.526 | Streamlit signs in a hand-off | identity and token from the proxy's headers, auth method `handoff` |
| 1.527 | component sandboxes | `allow-popups-to-escape-sandbox` in both; no `allow-top-navigation` |
| 1.528 | `lex_view` base | validated `lex_app_origin` is the base and event origin; foreign/malformed ignored; loopback only locally |
| 1.529 | `prune_dashboard_sessions` | deletes expired codes and revoked/ended/expired/unused bindings; keeps live ones; dry run; CASCADE |
| 1.530 | digests only | no code, state or handle in clear in the database |
| 1.531 | ALLOWED_HOSTS | every app host of `LEX_INSTANCE_HOSTS` (no port) is allowed; the custom domain reaches the hand-off |
| 1.532 | migration | nothing planned for `authentication` |
| 1.533 | cross-site WebSocket (BUG-039) | a socket from another origin or `null` → 4403 before any credential, after accept() with the hand-off, in the handshake in legacy; nothing upstream; own origin and no Origin relayed |
| 1.534 | dual: dead handle | falls through to the legacy session; the legacy answer clears the handle; `/auth/token` renews through legacy; socket relayed as legacy; dead handle alone → 401 |
| 1.535 | no off-origin target (BUG-040) | `/%2Fhost/`, `/%5Chost/`: marker redirect, hand-off `return`, cookie-blocked page targets, 503 refresh, legacy `auth_token` strip all stay on the dashboard origin |
| 1.536 | redeem lock | the one locking read is `FOR UPDATE OF` the code table; the sign-in row is read, never locked |
| 1.537 | daily re-set claimed only where it can be set | `/auth/token` and a WebSocket handshake leave `cookie_set_at`; the page re-sets for 30 days |
| 1.538 | pull cookie name | https proxy (`STREAMLIT_URL` or `SESSION_HTTPS_ONLY`): unprefixed `lex_dash` ignored, `__Host-lex_dash` works; plain http: unprefixed works |
| 1.539 | sign-in watcher | `/auth/status` 200 / 401 `session_expired` (binding revoked) / 401 `not_authenticated`, no-store, no cookie, 404 in legacy; every hand-off page carries the watcher, which runs in the dashboard document once, asks every minute while visible and on becoming visible, and on 401 goes to `/auth/required` (run under node) |
| 1.540 | deactivated user | binding revoked `user_inactive`; pull 401; socket 4401; status 401; document → fresh hand-off, handle cleared; nothing upstream |

**Scenario range:** 1.501 – 1.540. **Test file:** `lex/test_project/tests/init/test_1be_streamlit_handoff.py`. **Type:** U and I (transactional where the proxy's worker threads must see the rows). **Status:** ✅ Complete (2026-11-06). Batch: [1be](batches.md).

### 1bf. MCP embeds sign in with a single-use ticket, into a read-only cookie ✅

**What it tests:** plan "long-lived sessions" §6.5 (MCP embed). `lex_embed_view` used to put the MCP client's access token in the frame URL (`auth_token`). Now the URL carries `lex_embed_ticket` (single-use, 60 s); the token stays server-side in a refresh-less `OIDCSession`; a middleware redeems the ticket into `__Host-lex_embed`, which lives no longer than the token and may only read.

**Why a regression matters:** a bearer token back in URLs (history, logs, `Referer`), a replayed ticket signing someone in, or a `SameSite=None` cookie that can write -- a cross-site request forgery channel.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.541 | a ticket for a valid token | opaque ticket; token kept encrypted in a refresh-less sign-in; digest, `aud=embed`, frontend origin, 60 s |
| 1.542 | redeem | 302 to the URL without the ticket (other parameters kept); cookie attributes; Max-Age ≤ the token's life |
| 1.543 | once | a used ticket sets nothing; the replay ends the first embed |
| 1.544 | 60 seconds | an older ticket sets nothing |
| 1.545 | its own frontend | another host gets nothing; the ticket is spent |
| 1.546 | reads as the MCP user | `api/auth/session` with the token's roles; a login-protected read is served |
| 1.547 | never writes | POST/PUT/PATCH/DELETE → 403 `permission_denied` before the view; OPTIONS passes |
| 1.548 | dies with the token | 401 and the cookie removed |
| 1.549 | a real sign-in wins | the session user, not read-only |
| 1.550 | no ticket for bad tokens | forged, wrong audience/issuer, expiring, missing → nothing stored |
| 1.551 | the MCP tool | ticketed URL only in the result's `_meta` (the widget's); content, structured content and narration carry the clean `view_url`, no ticket, no token; caller `auth_token` dropped |
| 1.552 | back-channel logout | ends the embed |
| 1.553 | no redeem by a write | a POST never spends the ticket |
| 1.554 | ticket redirect stays on origin | paths decoding to `//host` or `\host` redirect to a path on the app origin, other parameters kept |
| 1.555 | the holder may render again | the frame holding the ticket's embed presents it again → ticket stripped, no new cookie, embed kept; anyone else's replay revokes |
| 1.556 | HEAD never redeems | no cookie, ticket unused, no binding; the GET then redeems |

**Scenario range:** 1.541 – 1.556. **Test file:** `lex/test_project/tests/init/test_1bf_mcp_embed_ticket.py`. **Type:** I (transactional). **Status:** ✅ Complete (2026-11-06). Batch: [1bf](batches.md).

### 1bg. Reflex dashboards stay signed in as long as Keycloak keeps the session ✅

**What it tests:** `LexKeycloakAuthState`, the Reflex Enterprise provider that keeps a dashboard's own Keycloak tokens (decision D5), hardened per plan "long-lived sessions" §6.4: the `at_hash` an ID token is checked against (where the tab checks one) comes from the access token the tab holds now; only `invalid_grant` signs the dashboard out (configuration errors keep the tokens with an ERROR, outages keep them and back off, and every refresh that keeps them re-arms the browser's timer; a userinfo 401 gets one refresh, a userinfo outage serves the same user's cached profile while the access token is valid); once the access token has expired and cannot be renewed, nobody is served -- no profile, permissions or token, `/login` says sign-in is temporarily unavailable -- yet the user is not signed out (the Reflex equivalent of the HTTP 503); Keycloak's answer is verified before it is stored (30 s leeway, one key-set refetch for an unknown `kid`); one refresh per refresh token across tabs (process memory, swept and capped) and replicas (Redis lock `lex:rx:refresh:{sha256(rt)}`, kept alive while its leader works; Fernet-encrypted set at `lex:rx:refreshed:{sha256(old rt)}` for 120 s, sealed for that refresh token and adopted only by the same user and Keycloak session); a second refresh within 30 s is suppressed (a clock that stepped back suppresses nothing); cookies last 30 days; Keycloak's back-channel logout signs the dashboard out through `lex:kc_sid_ended:{sid}`, audited once.

**Why a regression matters:** dashboards sign users out during a Keycloak blip, a misconfiguration, a clock skew or a key rotation, two tabs refresh each other's tokens once a second all day (armira, 2026-10-28), every tab hammers Keycloak, a dashboard stays signed in after the user was signed out everywhere else, or a dashboard serves an identity Keycloak no longer vouches for -- or another user's.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.561 | another tab's refresh is accepted | the plugin alone rejects B's refreshed pair (`at_hash`); lex accepts it and resolves the profile; one token call in all |
| 1.562 | a mismatched pair fails, no check invented | ID token of one exchange + access token of another fails (the plugin alone accepted it); a cookie-only tab keeps no at_hash |
| 1.563 | `invalid_grant` signs out | the scheduled refresh tells the browser `reset_auth`; INFO names `invalid_grant`; no back-off |
| 1.564 | configuration errors keep the tokens | `invalid_client`, `unauthorized_client`, `invalid_scope`, `invalid_request`, issuer `invalid_grant`: cookies unchanged, ERROR naming the client settings, no call within 300 s, a call after |
| 1.565 | outages keep the tokens and back off | 503 page, 502 `temporarily_unavailable`, 429, connect timeout, refused: tokens kept, WARNING, no call within the back-off, 5 s then 10 s, recovery clears it; never a sign-out |
| 1.566 | expired, Keycloak cannot renew (revised in review) | unreachable or `invalid_client`: no permissions (not even the grant cached for the expired token; `has_permission` refuses), no access token, no profile; cookies kept, no sign-out, `sign_in_unavailable`, refresh re-armed; Keycloak back → the same user, no new sign-in |
| 1.567 | userinfo 401 | exactly one refresh (the tab's refresh token), one retry with the new access token |
| 1.568 | userinfo outage | cached profile; a new tab gets its verified ID token's claims; no refresh, no sign-out |
| 1.569 | tabs refreshing at once | one token call; one `refreshed`, the others `adopted` the same new token through `_set_tokens` |
| 1.570 | the published set lives 120 s | a stale tab adopts it at 100 s without a call; at 130 s a stale tab asks Keycloak |
| 1.571 | the refreshing tab fails | the waiting tab keeps its tokens (`lock_wait`), then backs off with the failed refresh token; one call |
| 1.572 | cooldown | a second refresh 10 s later is `suppressed` (current token returned, no call, no sign-out); 31 s later it refreshes |
| 1.573 | 30-day cookies | lex's four cookies 30 d; the plugin's provider and module keep 7 d; the post-import check accepts lex's class, ERRORs on a short one |
| 1.574 | supported plugin release | 0.9.7 passes and is what `requirements.txt` pins; 0.9.8 logs a WARNING naming both and the module |
| 1.575 | Redis configured but down | tabs still share one refresh per round (process memory); one WARNING, not one per operation |
| 1.576 | across replicas (real Redis, opt-in) | one call; the set is in Redis under the frozen key, PTTL ≤ 120 s, a Fernet token with no token in clear; lock released; another replica adopts it |
| 1.577 | back-channel logout | the POST records `lex:kc_sid_ended:{sid}` (no Django sign-in matched); that session's tabs are signed out at validation and at the scheduled refresh without a Keycloak call; another session untouched |
| 1.578 | no stale-marker loop | tokens issued after the logout validate; a logout without `sid` records nothing |
| 1.579 | the marker in Redis (opt-in) | another Redis client reads it, TTL 90 d, value = the logout time |
| 1.580 | the profile cache is not a sign-in (review) | lex's per-event middleware keeps a valid profile, drops it once the access token expired; the next read renews silently: same user, one refresh, no new sign-in |
| 1.581 | `/login` waits for the renewal (review) | the page shows "Sign-in is temporarily unavailable. Retrying"; `start_login` re-arms the refresh, no Keycloak redirect, no button; on success the browser returns to `redirect_to` (`/fund`) |
| 1.582 | never another user's profile (review) | Ada's cached profile + Bob's tokens in the jar + userinfo 503: Bob's verified ID-token claims, Ada's profile no longer cached |
| 1.583 | clock skew (review, BUG-050) | Keycloak 2 s ahead: stored, signed in; 600 s ahead: refused before storing, tokens kept, ERROR names the clock, back-off, no sign-out |
| 1.584 | key rotation (review, BUG-051) | a new key published beside the old: one more key-set fetch, refresh stored, a new sign-in valid; an unpublished key: refused before storing, tokens kept, no second fetch within the minute |
| 1.585 | a kept refresh is retried (review) | 503, `invalid_client`, back-off and cooldown each send `trigger_access_token_refresh` |
| 1.586 | the lock outlives a slow leader (review) | lock 0.2 s, Keycloak 0.6 s, second tab at 0.3 s: one call; `refreshed` + `adopted`; no sign-out |
| 1.587 | planted sets are not adopted (review) | another user's set, a set sealed for another refresh token, another Keycloak session's set: Keycloak is asked, still Bob in `s-bob` |
| 1.588 | process memory is bounded (review) | 500 expired sets + one write → 1 entry; a store capped at 3 keeps the 3 expiring last |
| 1.589 | a clock stepped back (review) | refreshed, clock −1 h: the next refresh is made, not suppressed |
| 1.590 | one audit entry (review) | back-channel sign-out at the scheduled refresh: one `session_expired` (`backchannel_logout`), no token call |
| 1.591 | the Redis lock outlives a slow leader (review, opt-in Redis) | lock 0.2 s in Redis, still there at 0.45 s; one call; the other replica adopts; released afterwards |

**Scenario range:** 1.561 – 1.591 (1.592 – 1.600 unused). **Test file:** `lex/test_project/tests/init/test_1bg_reflex_session_hardening.py`. **Type:** U (1.561–1.576, 1.580–1.591), I (1.577–1.579). **Status:** ✅ Complete (2026-11-06; review fixes the same day). Batch: [1bg](batches.md).

### 1bh. The shipped sign-in helper for tests (`lex.lex_app.tests.auth`) ✅

**What it tests:** `sign_in(test_case, client, user, client_roles=..., uma_permissions=...)`, the supported way for lex's and downstream projects' tests to sign in under lex-app 2.5, where roles come only from the OIDC sign-in and a signed-in write asks Keycloak for UMA permissions before the view runs.

**Why a regression matters:** every downstream suite breaks on upgrade, or sends test tokens to the developer's real Keycloak.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.601 | roles reach lex | `api/user/` answers exactly the given client roles and the e-mail |
| 1.602 | writes never reach Keycloak | a signed-in POST reaches its view (405), the UMA stub answered once with the session's own token, no 401/503 |
| 1.603 | stubbed UMA is lex's answer | `api/user_permissions/` returns the given permissions in ra-rbac form |
| 1.604 | nothing outlives the test | after the cleanups: real UMA function, original `AUTHENTICATION_BACKENDS`, empty UMA cache |
| 1.605 | why it exists | `force_login` + old `session["oidc_userinfo"]` carries no roles in 2.5 |
| 1.606 | a method's own `@override_settings` does not leak | after a test whose method is decorated with `@override_settings` and calls `sign_in`: neither the method's setting nor the sign-in backend remains |

**Scenario range:** 1.601 – 1.606. **Test file:** `lex/test_project/tests/init/test_1bh_test_sign_in_helper.py`. **Type:** I. **Status:** ✅ Complete (2026-11-06).

### 1bi. The lex CLI answers a failing management command like manage.py ✅

**What it tests:** `lex <command>` for a forwarded Django management command that raises `CommandError` (`lex oidc_check` finding a problem, a missing argument).

**Why a regression matters:** operators run `lex oidc_check`, `lex oidc_cleanup` and friends from the sign-in docs and from the daily maintenance job; an expected answer must read as one line with an exit code, not as a crash.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.607 | one line and its exit code | `lex oidc_urls` without its host exits 1, stderr has `CommandError: Error: the following arguments are required: host`, no traceback |
| 1.608 | `--traceback` keeps the exception | `lex oidc_urls --traceback` lets the `CommandError` reach the caller |

**Scenario range:** 1.607 – 1.608. **Test file:** `lex/test_project/tests/init/test_1bi_cli_command_errors.py`. **Type:** I. **Status:** ✅ Complete (2026-11-06). Batch: [1bi](batches.md).

---

### 1bp. A frame another application put on screen does not get to move that tab ✅

**What it tests:** who the proxy's framed-401 page is allowed to navigate. The breakout exists because Keycloak refuses to be framed, so an unauthenticated frame must not be redirected to the IdP; it answers with a 401 page carrying three layers — an automatic top-level navigation, a `lex-auth-required` `postMessage`, and a `target="_top"` link. The automatic layer now runs only when `Sec-Fetch-Site` says this origin is also the one framing it (or when a client names no embedder, the pre-existing behaviour 1.214 pins). A frame on someone else's page keeps the 401, the message and the link, and takes nothing.

**Why a regression matters:** the top window belongs to the page the user is reading. A frame that cannot authenticate has learned something about itself, not about that page — and moving the window anyway is how a sidebar click inside an instance ended up parked on the dashboard host with an SSO round trip already completed. Narrowing it too far is the opposite failure: 1.669 and 1.670 are the counterweight, because withholding the navigation from everything strands the dashboard's own recovery.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.667 | the shell's frame leaves the shell's tab alone | `same-site` → no `window.top.location`; still 401, `lex-auth-required`, `target="_top"` |
| 1.668 | an unrelated embedder has no more claim | `cross-site` → the same |
| 1.669 | this origin's own frame still recovers | `same-origin` → the automatic navigation is kept |
| 1.670 | a client that names no embedder is unchanged | absent `Sec-Fetch-Site` → pre-existing behaviour |
| 1.671 | the embedder check is a header read | casing cannot defeat it |
| 1.672 | the frame the report builds is let in | the URL the real `Streamlit` report produces, loaded as a framed document against the real proxy app, banks the token, is redirected without it and ends on the dashboard's own bytes |
| 1.673 | the fault and the amplifier, observed together | the URL the report produces with no session token still 401s, and that 401 moves no window |

**Scenario range:** 1.667 – 1.673. **Test file:** `lex/test_project/tests/init/test_1bp_frame_does_not_take_the_tab.py`. **Type:** U. **Status:** ✅ Complete (2026-11-03). Batch: [1bp](batches.md).
### 1az — a machine endpoint on the dashboard origin cannot take over the user's tab

**What it tests:** the Streamlit proxy serves pages a person looks at and endpoints that exist only for code, on one host. Its re-auth machinery — whose purpose is to move the *top* window and then return the browser to whatever 401'd — may now only be reached by the first kind.

**Why a regression matters:** LEX-1477. A hidden, zero-sized `/_lex/theme-relay` frame 401'd on a proxy that did not serve that route yet, got the frame-breakout page, and sent the whole tab to `/auth/login?next=/_lex/theme-relay`; the OIDC round trip then parked it on a blank storage document. Intermittent, silent, and unconnectable to a theme shim by anyone looking at it.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.415 | a utility path is never a return target | `_safe_next_path` rejects `/_lex/*`, `/_stcore/*`, `/auth/token`, `/auth/adopt`; pages with query and fragment survive |
| 1.416 | a stashed utility path lands on `/` | `auth_callback` re-validates and falls back rather than honouring it |
| 1.417 | no return target for a top-level utility visit | the login redirect carries no `next` |
| 1.418 | a hidden utility frame gets a 401, not a breakout | JSON 401, no `window.top.location` anywhere in it |
| 1.419 | a framed dashboard still breaks out | the 401 HTML breakout, still carrying the view as `next` |
| 1.420 | a top-level visit is sent back to the frontend | 303 to the configured frontend origin |
| 1.421 | an unconfigured deployment does not bounce to localhost | the relay is served instead |
| 1.422 | the framed relay is untouched | 200, the shared key, `frame-ancestors <frontend>` |

**Scenario range:** 1.415 – 1.422. **Test file:** `lex/test_project/tests/init/test_1az_proxy_tab_takeover.py`. **Type:** U. **Status:** ✅ Complete (2026-10-26). Batch: [1az](batches.md). Frontend twin: `process-admin-general-client` batch F12u.
### 1ay — `ALLOWED_HOSTS` accepts the instance's own backend Service name

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.410 | short Service name accepted | `lex-backend-<instance>` is among the derived hosts |
| 1.411 | the three longer DNS forms accepted | `.<ns>`, `.<ns>.svc` and `.<ns>.svc.cluster.local` are all present |
| 1.412 | nothing added off-cluster | no instance identifier yields `[]`, whatever the namespace says |
| 1.413 | unreadable namespace degrades | an empty namespace yields exactly the short name, and does not raise |
| 1.414 | the namespace file is read safely | `_k8s_namespace` on a missing path returns `""` rather than raising |

**Scenario range:** 1.410 – 1.414. **Test file:** `lex/test_project/tests/init/test_1ay_allowed_hosts_in_cluster.py`. **Type:** U. **Status:** ✅ Complete (2026-10-25). Batch: [1ay](batches.md).

---

### 1bj. The initial-data load survives restarts and a second server process ✅

**What it tests:** the load `lex start` runs when every seed model is empty ends in one of two states, the whole seed or a clear report, however many server processes come and go during it. It runs in a non-daemon thread (a stopped process waits for it), one process decides at a time under a PostgreSQL advisory lock, and `lex_initial_data_load_state` records whether the last load finished.

**Why a regression matters:** a load cut off mid-write leaves rows the "models are empty" gate reads as existing data, and the seed is never loaded again, silently.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.609 | not a daemon | the load thread started from `lex-async-ready` has `daemon=False` and loads the seed |
| 1.610 | the second process waits | it decides only after the other load ended, and never loads a second copy |
| 1.611 | a killed load is reported | `interrupted` + ERROR, no load over partial rows |
| 1.612 | a stopping process gives up waiting | `abandoned` |
| 1.613 | MQ/Worker | inside `WaitForTasks` |
| 1.614 | the process waits for the load | a real interpreter exits only after the load |
| 1.615 | a failed load | recorded and reported at the next start |
| 1.616 | died before writing | loaded again |
| 1.617 | lock released | free after a load |
| 1.618 | existing data | the ordinary skip |

**Scenario range:** 1.609 – 1.618. **Test file:** `lex/test_project/tests/init/test_1bj_initial_data_load_restart.py`. **Type:** I. **Status:** ✅ Complete (2026-11-10). Batch: [1bj](batches.md).

---

### 1bk. The Streamlit handshake stays inside everyone's header budget ✅

**What it tests:** the auth proxy hands Streamlit the user's Keycloak access token in headers, so the token *is* a header line — and used to be three of them. Two limits sit on that line: `websockets` refuses one over `WEBSOCKETS_MAX_LINE_LENGTH` (8192 by default), and the ingress one over 16 KB. This batch covers the ceiling (`lex/ws_header_limits.py`, applied before anything can import `websockets`, reported when the installed release ignores it) and the single copy of the credential that now crosses to Streamlit.

**Why a regression matters:** the failure is silent. uvicorn's `websockets-sansio` handler neither answers the handshake nor closes the socket when the parse fails, so lex-1483 showed a dashboard that never loaded, an open connection, and no log line naming a header (LEX-894). The ceiling only works if it is installed before the library's first import, which nothing observable at runtime would reveal, and only if the installed library reads the variable at all — it has existed since websockets 13. Every one of these scenarios passes trivially with a small token and the old code, which is why they assert the mechanism rather than a size.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.619 | the ceiling is the default | importing `lex.ws_header_limits` with the variable unset leaves it at `65536`, well above the library's own 8192 |
| 1.620 | an operator's value wins | a configured `WEBSOCKETS_MAX_LINE_LENGTH` is never overwritten |
| 1.621 | applied in time | `lex/bin/lex.py` imports it before `uvicorn`, `lex/proxy.py` before `websockets` — asserted on the import statements, not on a mention |
| 1.622 | one copy, HTTP | a proxied request carries the token as `Authorization` only; `X-Forwarded-Access-Token` and `X-Streamlit-Access-Token` are gone |
| 1.623 | not two Authorizations | a caller's own `Authorization` never travels beside the proxy's |
| 1.624 | one copy, handshake | the WebSocket handshake carries it once, and `bearer_from_headers` still reads it |
| 1.625 | a ceiling not in force says so | a websockets release parsing below the configured limit produces a warning naming both numbers |

**Scenario range:** 1.619 – 1.625. **Test file:** `lex/test_project/tests/init/test_1bk_streamlit_header_budget.py`. **Type:** U. **Status:** ✅ Complete (2026-11-10). Batch: [1bk](batches.md).

### 1bm. Custom domains — every attached hostname is accepted, and a dashboard is framed same-site ✅

**What it tests:** an instance with a customer-owned domain attached answers on more than one hostname per role, and the pods behind the ingress have to agree. `lex/hosts.py` is the single place that knows which hostnames those are — read from `LEX_APP_HOSTS` / `LEX_STREAMLIT_HOSTS` / `LEX_REFLEX_HOSTS`, falling back to `DOMAIN_HOSTED` / `STREAMLIT_URL` / `REFLEX_URL` when nothing is configured. From there they reach Django's `ALLOWED_HOSTS` and `CSRF_TRUSTED_ORIGINS`, the Streamlit proxy's OIDC callback and token-renewal allow-list, and Reflex's websocket CORS. The second half is which hostname a dashboard is *framed* from: the one sharing a registrable domain with the page embedding it.

**Why a regression matters:** both halves fail silently and look like something else. A hostname missing from `ALLOWED_HOSTS` makes the customer's own domain answer "400 Bad Request" although DNS, Cloudflare and the ingress are all correct. A callback built from the canonical host while the user is on another one sets a host-only session cookie on a host the browser then leaves — an endless sign-in loop with every individual step succeeding. And a dashboard framed across registrable domains is a third-party iframe: it works in Chrome today and has never worked in Safari, which reads as "Streamlit is broken for this one customer".

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.632 | no custom domain, no change | each role reports its canonical host alone when no `LEX_*_HOSTS` is set |
| 1.633 | the list is parsed leniently | spacing, scheme, case and duplicates normalise away; the canonical host stays first |
| 1.634 | registrable domain | `customer.com`, a deep subdomain and `customer.co.uk` all reduce correctly |
| 1.635 | same-site dashboard | a viewer on `app.customer.com` gets `streamlit.`/`reflex.app.customer.com`; one on the platform host keeps the platform dashboard |
| 1.636 | fallback keeps scheme and port | an unmatched viewer gets the canonical URL; a local run keeps `http://localhost:8501/8502` |
| 1.637 | the middleware supplies the viewer | the request's host is the default, and does not outlive the request |
| 1.638 | the Streamlit report | iframe src is the same-site dashboard, still `?embed=true` |
| 1.639 | the Reflex report | iframe src is the same-site dashboard, still `?lex_embed=1` |
| 1.640 | Django accepts both hosts | both reach `ALLOWED_HOSTS`; both get an exact `https://` CSRF entry; the middleware is installed |
| 1.641 | nothing new is accepted | with no host list, `ALLOWED_HOSTS` is exactly what it has always been |
| 1.642 | the callback follows the user | `_external_base_url` / `_callback_url` name the hostname the request arrived on |
| 1.643 | a forged Host cannot steer it | an unrecognised `Host` falls back to the canonical URL |
| 1.644 | renewal from any attached host | every attached app and dashboard origin is trusted; `*` never is |
| 1.645 | Reflex websocket origins | every attached reflex host is an allowed origin, canonical first |
| 1.646 | a local Reflex run | no origins resolved, so Reflex keeps its own default |
| 1.647 | a hardened instance | `LEX_CORS_ALLOWED_ORIGINS` keeps the operator's list AND the instance's own hostnames |
| 1.648 | `lex_view()`'s frame | points at the app host on the dashboard's own domain; an explicit `REACT_APP_URL` still wins |
| 1.649 | the sign-in round trip | `/auth/login` through the whole app: the IdP redirect's `redirect_uri` is on the hostname the browser used |
| 1.650 | a forged `X-Forwarded-Host` | falls back to the canonical callback; the forged name appears nowhere in the IdP redirect |
| 1.651 | the renewal preflight | allowed and named from an attached app origin, with credentials; a foreign origin gets 403 |
| 1.652 | what Reflex resolves | `api_url` and the event URL stay on a host in Reflex's own `SAME_DOMAIN_HOSTNAMES`; CORS lists both hostnames |
| 1.653 | a malformed dashboard URL | an unparsable port degrades to the bare host instead of raising on every render |

**Scenario range:** 1.632 – 1.653. **Test file:** `lex/test_project/tests/init/test_1bm_custom_domain_hosts.py`. **Type:** U / I. **Status:** ✅ Complete (2026-11-10). Batch: [1bm](batches.md).

---

### 1bn. Scheduler bootstrap — the clock in the served backend, code-declared schedules, `run_scheduler` ✅

**What it tests:** Every instance gets a scheduler with no configuration and no extra pod: the clock starts only in the process serving the app, is on by default (off is a WARNING), survives failing passes; `@lex_job(schedule=...)` declarations become schedule rows kept in step with the code; `lex run_scheduler --once` does the same on demand; `ScheduledJob` is registered like any model and kept out of history.

**Why a regression matters:** Instances with no scheduler, clocks in the wrong processes, declared schedules drifting from the code, or a history table growing on every run.

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

**Scenario range:** 1.654 – 1.664. **Test file:** `lex/test_project/tests/init/test_1bn_scheduler_bootstrap.py`. **Type:** U + E. **Status:** ✅ Complete (2026-11-10). Batch: [1bn](batches.md).

### 1bq. `lex init` writes migrations for the project only, never into site-packages ✅

**What it tests:** that `lex init` makes migrations for the project's own app, and that no installed app's models drift from the migrations it has, so nothing is written into site-packages and a new database can store the API keys the framework creates.

**Why a regression matters:** a migration written into an installed package's folder conflicts with that package's next migration under the same number, and `lex init` stops. Without the api-key widening, a new instance cannot create an API key.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.674 | no file in site-packages | with a project app installed, `lex init`'s migration step asks makemigrations about that app only; `project_app_label()` is the project folder's app when settings installed it |
| 1.675 | the command line still decides | an app named on `--makemigrations-args` is what makemigrations gets |
| 1.676 | a framework checkout | without a project app, makemigrations runs for every app, as before |
| 1.677 | no app drifts | `lex makemigrations --check --dry-run` for every installed app finds nothing to generate |
| 1.678 | wide enough for a real key | an API key created on the migrated test database is stored with its 145-character id and found again by its key |
| 1.679 | one history, not two | lex overrides none of the api-key fork's migrations (no `MIGRATION_MODULES` entry), and the fork's own `0002` widens `id` and `hashed_key` to 150 under the name `lex init` used to generate |
| 1.680 | the id its migrations made | oauth2_authcodeflow's app config (django-oauth2-codeflow 3.0's own) pins `default_auto_field` to `AutoField`, and `BlacklistedToken`'s id is one |

**Scenario range:** 1.674 – 1.680. **Test file:** `lex/test_project/tests/init/test_1bq_init_project_only_migrations.py`. **Type:** U / I. **Status:** ✅ Complete (2026-10-12). Batch: [1bq](batches.md).


---

### 1br. lex_view filters

| # | Scenario | What We Assert | Status |
|---|----------|----------------|--------|
| 1.681 | Filters reach the component | Not in the URL; normalised dict and matching token as arguments | ✅ |
| 1.682 | Filters select the component | No plain iframe when `filters` is given | ✅ |
| 1.683 | No filter_bar (spec Amendment 18) | Passing `filter_bar` is a TypeError, as for any unknown keyword; nothing renders | ✅ |
| 1.684 | on_filter | `emit_filter=true` | ✅ |
| 1.685 | Malformed arguments | TypeError at the call | ✅ |
| 1.686 | A filter change is not a new frame | Same URL, different token | ✅ |
| 1.687 | When the shim posts | Loaded, named and changed only; index.html uses the function | ✅ |
| 1.688 | A long filters token | Over 2,000 characters it stays out of the URL and is posted after load; at 2,000 it rides the URL | ✅ |
| 1.689 | Every load resets the frame | After a new load, host filters that differ from the URL's are posted again; equal ones are not | ✅ |

---

### 1bs. `LEX_EMBED_ALLOWED_ORIGINS` — named sites only may embed lex-app and use its filter bridge ✅

**What it tests:** that embedding lex-app in a frame/iframe, and reading its filter bridge events, can be restricted to an operator-named list of sites. Amendment 14 of the multi-value-filters design adds `LEX_EMBED_ALLOWED_ORIGINS`, a comma-separated list of origins that becomes a validated Django setting (`parse_embed_allowed_origins` in `lex/runtime_config.py`), a `Content-Security-Policy: frame-ancestors` directive, and a `window.REACT_APP_EMBED_ALLOWED_ORIGINS` value the frontend reads from `config.js`. The directive is applied in two places: `EmbedXFrameOptionsMiddleware` (for a deployment that registers it) and, since `serve_react` is `@xframe_options_exempt` and therefore never covered by Django's own `XFrameOptionsMiddleware` at all, `serve_react` itself — on its SPA-fallback and other real `.html` document responses, which is what actually restricts who may frame lex-app as this repo ships today.

**Why a regression matters:** the branch this lands on adds a `filter` bridge event that reports a row count with no user action. `serve_react`'s `@xframe_options_exempt` means any site can frame the SPA document and pick up that event unless the CSP directive lands there specifically — a passive data leak, not just clickjacking, and one a middleware-only fix would miss entirely while registered nowhere. Getting the CSP merge wrong is just as bad in the other direction: clobbering an existing `Content-Security-Policy` header would be a worse regression than the one being fixed. A security review of the first cut found two further ways the fix itself could fail open rather than closed: `urlsplit` alone does not make a host safe to put in a header (a crafted bracketed host can inject an extra CSP source token; a non-ASCII host breaks the header or silently drops the directive), and `LEX_EMBED_ALLOWED_ORIGINS` set to only invalid entries parsed to `[]` — indistinguishable from unset, so a typo'd wildcard silently reopened unrestricted framing.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.690 | CSP added on every exempt path (middleware) | An `embed=true`, an OIDC-path and a cookie-marked response each carry `frame-ancestors 'self' <origins>`; a non-exempt response gets no CSP; an existing CSP header keeps its other directives, and a stale `frame-ancestors` directive is replaced rather than duplicated; configured but nothing valid still gets exactly `frame-ancestors 'self'` (fail closed, not wide open) |
| 1.691 | Unset/empty is a no-op (middleware) | With `LEX_EMBED_ALLOWED_ORIGINS == []` and `LEX_EMBED_ORIGINS_CONFIGURED == False`, no CSP is ever added, and the pre-existing behaviour holds: `X-Frame-Options` is stripped and the session/CSRF cookies are still patched to `SameSite=None; Secure` |
| 1.692 | `config.js` exposes the list | `window.REACT_APP_EMBED_ALLOWED_ORIGINS` is the comma-joined, `json.dumps`-escaped list, or `""` when empty; a build whose `config.js` predates the placeholder is served unchanged |
| 1.693 | Validation, normalisation, the real setting, and the fail-closed boundary | A path, a wildcard, a non-http scheme, credentials, a query string, a fragment and garbage are all dropped with one WARNING naming them; so are a bracketed/IPv6-shaped host, the `[v1.x https:]` CSP-injection shape, and hosts carrying `;`, a space, a quote, a backslash or `%`; an internationalised domain is accepted as IDNA punycode; `:443`/`:80` normalise away and a lone trailing `/` is accepted; valid origins are case-normalised and de-duplicated (including duplicates port-normalisation reveals); a stray/trailing comma is skipped quietly; a wildcard-only value fails closed with its own WARNING; `lex.lex_app.settings.LEX_EMBED_ALLOWED_ORIGINS`/`LEX_EMBED_ORIGINS_CONFIGURED` are exactly what `parse_embed_allowed_origins`/`embed_origins_configured` compute, verified through a real settings-module reload |
| 1.694 | The SPA document itself carries `frame-ancestors` | `serve_react`'s SPA fallback (`index.html`) and a literal non-fallback `.html`/`.htm` file (`embed.html`, case-insensitively) both carry `frame-ancestors 'self' <origins>`; merging keeps an existing CSP header's other directives; a hashed asset and `config.js` get none; unset leaves the SPA fallback unchanged; configured but nothing valid still gets exactly `frame-ancestors 'self'` |

**Scenario range:** 1.690 – 1.694. **Test file:** `lex/test_project/tests/init/test_1bs_embed_allowed_origins.py`. **Type:** U. **Status:** ✅ Complete (2026-10-17). Batch: [1bs](batches.md).

---

### 1bt. RFDS in the Reflex dashboards — configuration, Keycloak permissions, chrome, host bridge ✅

**What it tests:** that a project's Reflex dashboards get RFDS with nothing configured by hand: RFDS's plugins in the order `rfds.install()` demands with lex-app's brand and locale, RFDS installed on lex-app's runtime (Keycloak as the only identity, Keycloak's grants as RFDS's permissions, headers that let only lex-app frame a dashboard), `@lex_page` pages in lex-app's chrome, and a browser bridge that follows lex-app's colour mode and reports the frame's sign-in state.

**Why a regression matters:** a wrong plugin order or a missing plugin stops every dashboard from compiling; RFDS's default `X-Frame-Options: DENY` blanks every dashboard inside lex-app's Analytics tab while a missing `frame-ancestors` lets any site frame it; a resolver that answers without fresh grants shows controls a user has no grant for; a bridge that obeys any origin lets a stranger restyle a dashboard.

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

**Scenario range:** 1.695 – 1.706. **Test file:** `lex/test_project/tests/init/test_1bt_reflex_design_system.py`. **Type:** U. **Status:** ✅ Complete (2026-11-17). Batch: [1bt](batches.md).

---

### 1bu. Model data in the Reflex dashboards — through lex-app's own API, as the signed-in user ✅

**What it tests:** that a dashboard's model data comes through lex-app's own REST API as the signed-in user: the requests lex-app's frontend would make, errors that never look like empty data, RFDS's table filters, sort, paging and search translated to lex-app's list parameters, a grid within RFDS's state budget, and a record state that only hands out records the API would show.

**Why a regression matters:** a dashboard reading the database around lex-app's API would show records and fields the grid hides; a write around it would skip validation, hooks, the audit log and calculations; a mistranslated filter or ordering silently shows the wrong rows; an error mistaken for an empty page tells a user there is nothing when there is.

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

**Scenario range:** 1.707 – 1.716. **Test file:** `lex/test_project/tests/init/test_1bu_reflex_model_data.py`. **Type:** U (1.711: I). **Status:** ✅ Complete (2026-11-17). Batch: [1bu](batches.md).

---

### 1bv. lex-app's pages and controls inside Reflex dashboards — one embed contract for both frameworks ✅

**What it tests:** that a Reflex dashboard frames lex-app's pages and controls exactly as a Streamlit one does -- the same URL for the same arguments, the same events, the same manifest -- from one framework-free contract, with the Reflex additions a compiled page needs: records and filters as state vars, events to handlers, and the frame opening lex-app where the user reached it.

**Why a regression matters:** a second copy of the contract drifts, and lex-app's React app would see two dialects; a frame that believes any origin hands a stranger the dashboard's event handlers; a record frozen at compile time opens the wrong record; and moving Streamlit's code could silently break every Streamlit dashboard that imports it.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.717 | Streamlit and Reflex open the same URL | both render `build_lex_view_url`'s URL (Reflex adding the filters token Streamlit's shim adds in the browser); old Streamlit imports are the same objects; discovery never imports `lex_app.embed` |
| 1.718 | Handlers ask for their events and receive them | an `on_create` handler sets only `emit_create` and is wired; `on_event` alone sets every flag; none sets nothing; only lex-app's origin is believed |
| 1.719 | A record named by a state var is joined in the browser | a Var path and an f-string embedding a var become a Var URL with the base, the path and the embed query; filters stay the state var, not frozen into the URL |
| 1.720 | lex-app is framed from the app address beside the dashboards | `REACT_APP_URL`; else the app host on the dashboards' registrable domain; else `http://localhost:8000` |
| 1.721 | Widgets post one validated manifest with runtime records | one `/embed.html?embed=true` frame, version-1 manifest, a Var pk, ids unique per widget, `on_status` wired, the default log dialog unless the page handles logs; empty, duplicate-id and unknown-option specs refused at build |
| 1.722 | A calculation's log opens over the page | `open_log` builds a one-widget log manifest for the run and opens the dialog; an incomplete request changes nothing |
| 1.723 | The frame speaks lex-app's contract and trusts only lex-app | JS filters token equals Python's; the page opens on the framing lex-app origin, never a stranger's; each load posts theme then the missing filters; duplicates, foreign origins, other windows and newer versions ignored; widget host gets manifest and theme on ready; status reaches `on_status` |

**Scenario range:** 1.717 – 1.723. **Test file:** `lex/test_project/tests/init/test_1bv_reflex_lex_view_and_widgets.py`. **Type:** U. **Status:** ✅ Complete (2026-11-17). Batch: [1bv](batches.md).

---

### 1bw. A consultant's tooling for Reflex dashboards — lex reflex add, lex reflex check, the reference example ✅

**What it tests:** that a consultant gets a working page for a model with one command, checks dashboards the way CI does with another, and copies a reference structure module that uses every feature and compiles.

**Why a regression matters:** a template that imports a name lex-app no longer exports breaks the first compile a consultant runs; a page that is not registered silently never appears; a check that differs from CI passes locally and fails in the pipeline; an example nobody compiles teaches an API that is gone.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.724 | `add` writes a page and registers it once | `_dashboards/` package and page; `_reflex_structure.py` created with `main()` and the import once; refused without `--force`; `--no-register` and `object_page` leave the structure alone; bad model, block or route exit non-zero |
| 1.725 | Every template is valid and uses only lex-app's surface | each block parses and imports only names `lex.lex_app.reflex` exports; list and overview are `@lex_page` routes gated on read; object page is a `ui.State` dashboard for `reflex_main`, whose suggested import names the project's package; the overview's database read is behind its `@ui.load`'s `requires=` |
| 1.726 | `check` lints the dashboards and runs the doctor | `lint --strict` over the structure module and the package, then `doctor --app-root`; non-zero when either fails; only the doctor when there is nothing to lint |
| 1.727 | The reference example uses every feature and compiles | imports every feature lex-app gives a dashboard (pages, chrome permissions, tables, grid, lex_view with flows, widgets, the ORM bridge) and writes its handlers with lex.ui (loads, actions, the API as the user, refusals, status); its pages are in the compiled app under one navigation heading; the compile ran conformance in strict CI mode |

**Scenario range:** 1.724 – 1.727. **Test file:** `lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py`. **Type:** U. **Status:** ✅ Complete (2026-11-17). Batch: [1bw](batches.md).

---

### 1bx. Reflex dashboards sign in through lex-app's hand-off - no Reflex Enterprise, no Reflex account ✅

**What it tests:** that a Reflex dashboard signs in through lex-app's own sign-in, without Reflex Enterprise: the hand-off round trip and its cookies, the event-socket guard, the session middleware's outcomes (signed in, anonymous, ended, unreadable, blocked), RFDS's identity and lex_event's checks, the access token from Django's sign-in, the hand-off's error pages, sign-out, and export tickets bound to their owner.

**Why a regression matters:** without it a dashboard either cannot sign in without a Reflex account, or leaks its sign-in cookie into page-readable state, outlives a sign-out, runs a guarded handler for anyone, or hands one user's export to another.

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

**Scenario range:** 1.728 – 1.735. **Test file:** `lex/test_project/tests/init/test_1bx_reflex_sign_in_handoff.py`. **Type:** I (transactional; 1.731 and 1.735 partly U). **Status:** ✅ Complete (2026-11-19). Batch: [1bx](batches.md).

---

### 1by. Reflex dashboards without Reflex Enterprise - nothing imports it, and lex-app's own one-port proxy ✅

**What it tests:** that nothing lex-app ships imports or requires Reflex Enterprise, and that lex-app's own one-port proxy passes pages, the hot-reload WebSocket, and their bodies and subprotocols through to the frontend server -- only in a one-port development run.

**Why a regression matters:** a regression brings back reflex login (or a paid tier) for every developer and deployment, or leaves a pod whose pages all 404 because nothing passes them on.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.736 | Nothing imports Reflex Enterprise or requires it | child interpreter refusing reflex_enterprise and rfds.enterprise: config, every lex.lex_app.reflex module and lex.ui import, the config is Reflex's own, no Enterprise module loaded; requirements pin reflex and rfds==1.0.0, no reflex-enterprise |
| 1.737 | The proxy runs only for a one-port development run | REFLEX_USE_SINGLE_PORT set, development, not backend-only -> on; unset, production or backend-only -> off; install registers the lifespan task only when on |
| 1.738 | Pages pass through to the frontend server | method, path, query, body on; Host the upstream's; hop-by-hop dropped; status, headers, body back; gzip decoded without Content-Encoding; a starting frontend retried with the body every time; never answering -> 502 |
| 1.739 | The hot-reload socket passes through with its subprotocol | accepted with vite-hmr; text and bytes both ways unchanged; no frontend -> the socket is refused |

**Scenario range:** 1.736 – 1.739. **Test file:** `lex/test_project/tests/init/test_1by_reflex_without_enterprise.py`. **Type:** U. **Status:** ✅ Complete (2026-11-19). Batch: [1by](batches.md).

---

### 1bz. The LEX look for Reflex dashboards ✅

**What it tests:** that a Reflex dashboard looks like lex-app: every RFDS colour, type, radius and chrome value comes from lex-app's design tokens in light and dark, wins over RFDS's own stylesheets wherever RFDS sets it, the accent is the LEX teal itself, the header, navigation, cards and buttons are lex-app's, and pages open in light mode as lex-app does.

**Why a regression matters:** a dashboard framed inside lex-app that reads as another product (RFDS's slate canvas, cyan-indigo accent, square bordered cards, another font) -- which is what the first cut shipped, because RFDS's brand sheet outranked lex-app's overrides.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.740 | Every colour, type and radius is a lex token | per mode: canvas, surfaces, text, link, lines, teal accent and focus, tag statuses, navy chrome, table header, Inter, radii = the tokens; no hex literal in the module's code; a changed token changes the stylesheet |
| 1.741 | lex values outrank RFDS and reach every Radix element | both mode blocks start at html:root:root (above the brand sheet's :root:root) and cover forced-mode regions, dark last; every re-declared variable the theme sets is forwarded from a per-mode copy; every other one RFDS re-declares reads an accent step the theme sets |
| 1.742 | The accent scale is the LEX teal, not RFDS's adjusted brand | indigo-9 = TEAL 500 both modes; hover, accent text, selection washes, on-solid text and page/panel backgrounds from the tokens; the brand passed to RFDS is the same teal |
| 1.743 | Header, navigation, cards and buttons are lex-app's | navy gradient header with teal glow, no stripe; navy side nav, teal wash + 3 px bar on the current page; 16 px cards with soft elevation; buttons 8 px / 600 / no uppercase / hover lift, none with reduced motion; primary navy gradient + white (light), teal gradient + dark text (dark); soft focus ring, no dashed title outline |
| 1.744 | Pages load Inter and the theme and open in light mode | lex_config default_color_mode light; head links Inter and inlines the theme then the embedded layout; the embedded layout file carries no colours |

**Scenario range:** 1.740 – 1.744. **Test file:** `lex/test_project/tests/init/test_1bz_reflex_lex_look.py`. **Type:** U. **Status:** ✅ Complete (2026-11-19). Batch: [1bz](batches.md).

---

### 1ca. A local run lets the dashboards' sign-in and sign-out send the browser back to them ✅

**What it tests:** that in a local run the dashboards' dev servers (Streamlit 8501, Reflex 8502 and 8503, or a moved Reflex port) are valid `next=` targets for lex-app's sign-in and sign-out, and that nothing else new is.

**Why a regression matters:** a developer cannot sign out of (or in to) a local dashboard -- the browser ends on "Redirect target not allowed." -- or, the other way, a local run or a deployed instance trusts origins it should not: an open redirect.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.745 | A local run trusts the dashboards' dev servers and no more | local: 8501, 8502, 8503 and a moved Reflex port trusted on localhost and 127.0.0.1, total_logout and authenticate redirect back to the dashboard; another loopback port and a foreign site still 400 REDIRECT_NOT_ALLOWED; deployed: no localhost; this local run's real settings carry the dashboards' origins |

**Scenario range:** 1.745 – 1.745. **Test file:** `lex/test_project/tests/init/test_1ca_local_dashboard_redirects.py`. **Type:** I. **Status:** ✅ Complete (2026-11-20). Batch: [1ca](batches.md).

---

### 1cb. lex-app's grids and dashboard pages take the page's width ✅

**What it tests:** that a grid built with `lex_grid`, and every dashboard page lex-app draws (`/` and the Analytics tab), fill the page's width instead of shrinking to their content.

**Why a regression matters:** a grid that shows one column and no visible rows, or a dashboard squeezed into a narrow strip -- what a start-aligned `rx.vstack` does to RFDS's grid and to a project's page.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.746 | Grids and dashboards fill the page width | lex_grid's stack stretches its children and RFDS's grid frame stays 100% wide; the stack directly inside the shell's main region stretches, so a project's page, tables and cards take the frame's width |

**Scenario range:** 1.746 – 1.746. **Test file:** `lex/test_project/tests/init/test_1cb_reflex_full_width.py`. **Type:** U. **Status:** ✅ Complete (2026-11-20). Batch: [1cb](batches.md).

---

### 1cc. lex.ui results and thread pools ✅

**What it tests:** the four values a handler may return or yield and how each becomes a browser event (an in-app path is a redirect, another site a whole-page navigation, a toast RFDS's toast, a download Reflex's download), and the two thread pools handler bodies run on, sized by `LEX_UI_ACTION_THREADS` (16) and `LEX_UI_JOB_THREADS` (8).

**Why a regression matters:** a handler whose result is silently dropped, a wrong value that is accepted instead of refused with the handler's name, or long jobs and page loads sharing one pool so that an export starves every other click.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.747 | A handler returns plain values and nothing else | goto, toast and download are results; progress is the progress report, clamped to 0..1; None is nothing; a wrong tone, an empty path, a download of nothing and any other object (a raw Reflex event) are refused with the handler's name |
| 1.748 | Values become browser events and are recorded for tests | an in-app path is Reflex's redirect, another site (a full URL, or //host) a whole-page navigation, the toast RFDS's toast of that tone, the download Reflex's download; with the test recorder on, the values themselves are recorded in order |
| 1.749 | Bodies run on sized pools with the caller's context | functions run off the calling thread on a thread named for their pool, and see the caller's context variables; the sizes come from LEX_UI_ACTION_THREADS and LEX_UI_JOB_THREADS (16 and 8 by default, at least 1); a full pool queues: on a one-thread pool a second call waits its turn and does not fail |

**Scenario range:** 1.747 – 1.749. **Test file:** `lex/test_project/tests/init/test_1cc_ui_results_and_pools.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cc](batches.md).

---

### 1cd. lex.ui guard: requires values, the query block, the acting user ✅

**What it tests:** what `requires=` accepts (nothing, one value or a list, `ui.read(Model)`, `ui.signed_in`, a named resource) and how the user's model-wide Keycloak grants are checked against it; the block that refuses ORM queries in a handler that declared nothing; and the operation context that makes ORM writes name the signed-in user.

**Why a regression matters:** a dashboard showing rows the grid would hide (the ORM applies no hidden fields or row rules), a record-scoped edit grant treated as access to the whole model, an empty `requires=` that guards nothing, or created_by / edited_by naming nobody.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.750 | requires= values and the grant check | nothing declared is None; one value or a list is a tuple; ui.read(Group) holds; a record-scoped edit does not open the model; a list needs all; ui.signed_in always holds; a named resource works like a model; an empty list is refused |
| 1.751 | Queries are refused inside the block and work after it | inside block_queries a query raises UnguardedQuery naming the handler and the two ways out (self.api, requires=); after the block queries work again |
| 1.752 | The acting user is set for audit fields | inside acting_user lex-app's operation context carries a request whose user is authenticated and reads as the username (the e-mail when there is none, the user's id when there is neither); it is restored afterwards |

**Scenario range:** 1.750 – 1.752. **Test file:** `lex/test_project/tests/init/test_1cd_ui_guard.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cd](batches.md).

---

### 1ce. lex.ui working copy: snapshot, restore, draft, merge ✅

**What it tests:** the snapshot of a state's own fields and its restore and reset, the draft a job body sees as `self` (fields, computed values, helpers, and the misuse it refuses), and the merge that writes back only what the draft changed.

**Why a regression matters:** a job overwriting the user's input with stale values, half-finished changes staying on screen after an error, a field the state cannot copy failing somewhere unrelated instead of by name, or a NaN or array value read as "changed".

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.753 | A snapshot holds the state's own fields and restores them | the snapshot holds deep copies of the class's own fields only; restore puts every changed field back; reset sets fields to their class defaults except the kept one; a field holding an uncopyable object makes snapshot raise a TypeError naming it |
| 1.754 | A draft reads and writes fields and reaches helpers | fields behave as on the state; a computed value and an underscore helper work on the draft's values; assigning an undeclared field and calling another event handler are refused with a message that says what to do; so is reading an inherited field |
| 1.755 | Merge writes only what the draft changed | the first merge writes progress and rows and leaves the user's note; the draft then sees the user's note; when both changed the same field the job's value wins; a NaN or a value whose != is not a bool is compared safely, not read as changed |

**Scenario range:** 1.753 – 1.755. **Test file:** `lex/test_project/tests/init/test_1ce_ui_working_copy.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1ce](batches.md).

---

### 1cf. lex.ui context: user, params, grants, the API as the user ✅

**What it tests:** the invocation a handler body reads its user, parameters and grants from, the bridge that waits on the event loop, and `self.api` (list with filters, search and ordering, get, create, update, delete) called as the user with a fresh token each time.

**Why a regression matters:** a handler acting as nobody or with a token that expired mid-job, a missing parameter that fails with an unhelpful error, a timed-out call that still completes later and duplicates a write, or a 403 that is not reported as a refusal.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.756 | User, params and grants are reachable from the body | the user's four names are there; a parameter reads as an attribute or with get(); a missing one is an AttributeError that says so; can() answers from the grants; outside a handler current() says it only works inside one; on_loop refuses the event loop's own thread, and on a timeout it cancels the coroutine so it never completes |
| 1.757 | The API is called as the user, with a fresh token each time | each call carries the token current at that moment as a bearer token; list sends the filters (a list as repeated values), the search on the model's text field and the ordering, and returns plain rows with the total count, trimmed to the fields asked for; get, create, update and delete hit lex-app's routes and return the row |
| 1.758 | A refusal is an API error and a stand-in replaces the API | 403 raises LexApiError with denied true; no token raises 401; with a stand-in, self.api is the stand-in itself |

**Scenario range:** 1.756 – 1.758. **Test file:** `lex/test_project/tests/init/test_1cf_ui_context.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cf](batches.md).

---

### 1cg. ui.State and handler status ✅

**What it tests:** what `ui.State` adds to a Reflex state, how a run's life is recorded (started, progress, finished or failed, overlapping runs for different rows, the registry that lets one run per key and argument in), and the view helpers `running`, `failed` and `progress_of`.

**Why a regression matters:** a spinner that never stops, a button that stays disabled after a failure, a second row's run clearing the first's mark, or a project writing its own busy flags again.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.759 | ui.State adds fields and helpers but no event handlers of its own | ui.State is a real Reflex state with the status fields and the visit counter; user, api, params, can, state and tab_id are properties, not event handlers; the project's state inherits the status fields and keeps only its own as its own; those six names are reserved -- a project state declaring a field (with or without a default), a computed var or a method of one of them is refused when the class is defined, with a message naming the state and the name and saying to rename it |
| 1.760 | Status marks follow a run's life | running lists the rows in flight; progress holds the fraction and the message; finishing removes the row and the progress, records the failure message, and a new start clears the previous failure; the registry lets one run per key and argument in, and a cancel request reaches every run of that handler |
| 1.761 | Views read status with running, failed and progress_of | each is a Reflex expression over ui.State's status fields under the handler's key, with a default when nothing was recorded; the per-row form compares the row as text |

**Scenario range:** 1.759 – 1.761. **Test file:** `lex/test_project/tests/init/test_1cg_ui_state_and_status.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cg](batches.md).

---

### 1ch. @ui.action: plain synchronous handlers and their failures ✅

**What it tests:** `@ui.action` end to end through `ui.testing`: the happy path, what the browser sees in order, a failure's discard and what the log and the user get, refusals and API answers shown in their own words, the ORM rule with and without `requires=`, and the event loop staying free while a body sleeps or is cancelled.

**Why a regression matters:** a slow click stalling every user, half-applied values after an error, an exception's text or a traceback reaching the browser, a state field that cannot be copied or a session that cannot be resolved leaving the handler marked running forever, or a database query running without a declared requirement.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.762 | A plain function runs off the loop on the live state | the body ran on an interactive pool thread and saw the user and the parameter; the fields changed and reached the browser; the toast was shown; nothing is running afterwards and no failure is recorded; an async def under @ui.action is refused |
| 1.763 | Running is shown first and each yield flushes | the browser first learns the handler is running, then sees steps=1 before the body has finished, then steps=2; running is cleared at the end; the goto is sent |
| 1.764 | A failure discards what was not shown and hides the detail | the fields are back at what the yield showed; the user gets an error toast with a reference and without the exception's text; the failure message is recorded for the view; the log holds the traceback by default and only the type with LEX_UI_LOG_ERROR_TEXT=false; an uncopyable field, an unresolvable session and a wrong yielded value each end as an unexpected error with nothing left running; a finally in the body runs before the restore. A body that calls another handler of its state (self.reload()) fails the action instead of silently doing nothing: its change is restored, the sibling never ran, and the log names both handlers and says to use an underscore method |
| 1.765 | Refusals and API answers are shown in their own words | a ui.Refuse message is shown as written, as a warning, and its changes are discarded; a 403 is the standard "not permitted" message; a validation error is shown with the API's detail -- lex-app's own refusal body by its detail alone, and a body without one as its fields' name: text pairs, never its code, error or traceback; none is reported as an unexpected error |
| 1.766 | The database is reachable only with requires | without requires= the query is refused and reported as an unexpected error whose log names the handler and the way out; with the grant the count is read; without the grant the body never runs, the user is told they are not permitted, that message is recorded as the failure and nothing is left running |
| 1.767 | The loop stays free and a slow action is called out | the event loop keeps ticking while a body sleeps; a 1.1 s run in development logs a warning that names the handler and says to make it a job; a cancelled body's thread finishes, nothing is left marked running, and a following action on the same tab still runs; a stopped body whose cleanup raises is logged as a warning, not swallowed |

**Scenario range:** 1.762 – 1.767. **Test file:** `lex/test_project/tests/init/test_1ch_ui_action.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1ch](batches.md).

---

### 1ci. @ui.job: long work on a working copy ✅

**What it tests:** `@ui.job` end to end: the user typing during a job, the job winning a same-field conflict, repeats ignored per tab and set of arguments, a full job pool queueing, cancel and stop-on-leave ending a job at its next yield with its cleanup, a cancellation during the wind-up, a failing job keeping what it had merged, and a job whose `requires=` is not met.

**Why a regression matters:** a frozen screen during an export, a job overwriting what the user typed, the same export running twice from repeated clicks, a Cancel that never takes effect or skips the job's cleanup, or a failed job that loses what it had already shown.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.768 | The user keeps working and their changes survive | the user's event returns at once; the job saw the old note until its next yield and the new one after it; at the end the job's fields and the user's note both stand; another state the job changed through self.state() is merged too; the toast is shown; the browser got progress at each yield |
| 1.769 | The job wins when both change the same field | a job that sets status and a user who sets it meanwhile: the job's value stands |
| 1.770 | One run at a time per tab and set of arguments | for "a" sent three times rapidly the job ran once and the repeats were ignored; "b" ran beside it; a later "a" after it finished ran again. A call with a different second argument runs beside the first (only the exact same arguments are a repeat) and the view still sees each run under its first argument; on a full one-thread job pool a second job waits its turn instead of failing |
| 1.771 | Cancel takes effect at the next yield | the job stops within a few steps, its finally block runs and it is no longer running; what the stopped body changed in its cleanup is never merged; ui.cancel(handler) is the event for a Cancel button. A job whose task is cancelled while its wind-up waits for the user's lock is still wound up: not left running, its failure recorded, its claim released |
| 1.772 | Stop on leave ends the job when the page changes | a job with stop_on_leave=True stops at its next yield when the user opens another page, and its cleanup runs |
| 1.773 | A failing job keeps what was merged and reports | what was merged at the yield stays, the later change is discarded, an error toast is shown, the failure message is readable with ui.failed, and it is no longer running. A job whose requires= is not met (under a store like Redis) never runs: the user is told they are not permitted, the message is recorded, the warning names the handler and the user's id, nothing is left running |

**Scenario range:** 1.768 – 1.773. **Test file:** `lex/test_project/tests/init/test_1ci_ui_job.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1ci](batches.md).

---

### 1cj. @ui.load: page loads, the opener, the error page ✅

**What it tests:** `@ui.load` for both kinds: the reset-then-fill order, the opener's order and permission check from the server's page registry, results that arrive after the user left, failures of required and optional loads, loads started on mount by a dashboard, and the wiring (`lex_page`, the error page, `create_app`).

**Why a regression matters:** a page flashing another record's data, a load starting before sign-in or under a permission the browser chose, a stale result overwriting a newer one, one broken region taking the whole page to the error page, or a dashboard whose data never lands because its mount event beat the opener.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.774 | A load resets the state, then fills it -- once per visit | the rows were emptied before the body ran, then filled from this visit's parameter; the kept field was not reset; the browser saw the empty rows first; within one visit the optional load does not reset what the required one filled, and the next visit's first load clears it. A dashboard's load started on mount lands after the opener (and after a navigation from another page), resets the state at every start, and leaves nothing running |
| 1.775 | The opener starts required loads, then optional ones | the on_load list is split, keeping handlers and leaving a plain Reflex handler in place; the opener takes only the route, checks the permission the page was registered with, starts a new visit, then the required load, then the optional one; a user lacking the permission or an unknown route starts nothing; each same-named twin page starts its own state's load. A load started on mount starts no optional load and leaves the opener's required load pending |
| 1.776 | Stale results are discarded | a result that arrives after the user opened another page is not written; a cancelled required load is not running, stays pending and starts no optional load. A load started on mount is dropped only when a newer run of itself starts (older finishing last or first, same argument overlapping); the newer run keeps its process record; a run whose browser moved on still fills its own state. A load whose task is cancelled while its wind-up waits for the user's lock is still wound up and not left running |
| 1.777 | A failed required load shows the error page, an optional one its region | a required failure records a reference and the page to retry (its query string kept, never the error page), sends the browser to the error page without the exception's text, ends the visit so a second failure changes nothing, and does nothing once the browser has moved on; an optional failure sends nobody anywhere and leaves its message for ui.failed. A dashboard's load fails in place: no redirect, no recorded error, the visit not ended, the message with a reference left for ui.failed; an earlier page's recorded error survives the next opener. "Try again" only ever returns to a path of this site (one leading slash, no backslash), else to /. A load the user may not run, under a store like Redis, never runs: the refusal is in ui.failed, the warning names the handler and the user's id, nothing is left running |
| 1.778 | lex_page adds the opener and the error page exists | a page's on_load is the access load, any plain handler, then the opener carrying only the route; the error page shows the message and a way to try again; create_app loads rfds.toast before it adds a page, so it is never first imported in the middle of an event; lex_page refuses a route whose dynamic argument is named like a ui.State helper, and a load declared with arguments; create_app, once the project's pages are declared, refuses any page route with such an argument |

**Scenario range:** 1.774 – 1.778. **Test file:** `lex/test_project/tests/init/test_1cj_ui_load.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cj](batches.md).

---

### 1ck. lex.ui clears runs whose process is gone ✅

**What it tests:** the sweep that clears the runs of a process that is gone (and keeps those of a live one), and how liveness is decided: locally only this process, with Redis a heartbeat key with an expiry, failing open on a Redis that errors, hangs or cannot be built.

**Why a regression matters:** a button that stays disabled until the user opens a new tab after a backend restart, a live worker's run cleared by mistake, or a hanging Redis stalling every page open.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.779 | A sweep clears runs of a process that is gone | the dead process's run is cleared with its progress; this process's run stays; a process another worker reports alive keeps its run; an argument containing "|" is cleared completely; a dead process is asked about once however many runs it had; two runs under one key and argument keep their process record until the last finishes, and a sweep clears them all; a liveness check that hangs ends the sweep within its budget and leaves the marks; opening a page sweeps |
| 1.780 | Liveness is local without Redis and a heartbeat with it | without Redis only this process is alive; with Redis a process is alive while its heartbeat key exists, and the heartbeat writes this process's key with an expiry; a Redis that errors, hangs or cannot be built leaves a process counted alive; a failed beat does not end the heartbeat; ending it deletes the key and closes the one client; a forked child gets its own id |

**Scenario range:** 1.779 – 1.780. **Test file:** `lex/test_project/tests/init/test_1ck_ui_stale_runs.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1ck](batches.md).

---

### 1cl. @ui.custom and the lex.ui findings of lex reflex check ✅

**What it tests:** `@ui.custom` as an ordinary Reflex handler carrying a mark, and the static check over project code: errors for unmarked raw handlers and for undecorated public methods of a `ui.State`, recognised through import aliases and across files by inheritance; one error for a file that cannot be analysed; errors for a field, computed var or method named like a body helper and for `self.<handler>()` of a sibling handler; notes for custom handlers and `requires=ui.signed_in`; a warning for a handler that names a model manager without declaring what it requires; and the exit code of `run_check` (non-zero on an error, 0 on warnings and notes only).

**Why a regression matters:** raw Reflex handlers creeping back into project code with no one seeing, an unguarded model manager passing review, or `lex reflex check` returning success because RFDS's checks passed while lex.ui's found an error.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.781 | ui.custom is a marked Reflex handler | an async handler under @ui.custom on a ui.State is an ordinary Reflex event handler -- async allowed, background on request -- that carries the custom mark |
| 1.782 | The check fails on unmarked raw handlers and lists the rest | the raw handler is an error; the custom handler and the signed_in requirement are notes; the unguarded manager is a warning; the guarded one and the plain state raise nothing; the report returns 1, and run_check returns non-zero even when RFDS's own checks pass. Undecorated public methods of a ui.State are errors (helpers, properties, static and class methods and computed vars are not); handlers are recognised through import aliases; a state inheriting a ui state from another file is checked; a file that cannot be analysed is one error naming it; a field, computed var or method named like a body helper (user, api, params, can, state) is an error; self.<handler>() of the state's own or an inherited handler, from any method, is an error that says to use an underscore method; run_check returns 0 when only warnings and notes are found |

**Scenario range:** 1.781 – 1.782. **Test file:** `lex/test_project/tests/init/test_1cl_ui_custom_and_check.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cl](batches.md).

---

### 1cm. The lex.ui namespace; templates and the reference example on the layer ✅

**What it tests:** the public `lex.ui` namespace against the implementation, the three templates `lex reflex add` writes, and the reference `_reflex_structure.py`: no async plumbing, every state a `ui.State`, nothing for either lint to report, and the example's behaviour for a viewer without, or with some, read grants.

**Why a regression matters:** a template or the reference example that teaches the old async plumbing again, a name missing or different from the implementation's, a dashboard that frames a record before it has arrived, or an export that gives out models the viewer may not read.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.783 | from lex import ui has every name | each public name is the implementation's own object, ui.ApiError is lex-app's API error, and ui.testing is the test helper module; nothing else is public beside the page names and the table and adoption names, which batches 1cu, 1cv and 1cy cover |
| 1.784 | The scaffold templates are written on the layer | none contains async, await, run_orm or lex_api (as a name or an attribute); every state with handlers is a ui.State; the object page and the overview use ui.State with @ui.load; neither the lex.ui check nor RFDS's strict lint reports an error or a warning on any of them; the object page shows its load's failure, else the loaded record, else a spinner; its load, started on mount, reports a refusal in place (in ui.failed, no redirect) |
| 1.785 | The reference example is written on the layer | outside RFDS's own async export hook it contains no async, await, run_orm or lex_api; every state with handlers is a ui.State; the lex.ui check reports no error and neither check a warning; the overview's load, started on mount, reports its refusal in place on `/` (in ui.failed, no redirect), with the export menu offered only while the load has not failed; a viewer who may not read the featured model is refused by the load (no redirect) and by the export; a viewer who may read some models sees the same counts on screen and in the export, only for those models; the record dashboard reads the model `?model=` names, refuses a missing or unknown one -- in ui.failed, with no redirect -- and shows failure, else the record, else a spinner; the edit page is a form submitted to one action, with no per-keystroke handler, and `save` writes the submitted values |

**Scenario range:** 1.783 – 1.785. **Test file:** `lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cm](batches.md).

---

### 1cn. lex.ui: a load says when it has finished; the page registry decides who owns the error page ✅

**What it tests:** A page shows a skeleton until its load has finished, so the layer says `loaded` (false before the load has started, true after it, and reset when the page is opened again), and where a load is registered, not its own `optional=`, decides whether its failure takes over the page (spec 5.2 and 6.4).

**Why a regression matters:** a page flashes its empty content before loading, a revisit shows the last visit's content, or a failed section replaces the whole page with the error page.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.786 | A load says when it has finished | ui.loaded(handler) is a view value that is false until the load has finished and true afterwards; opening the page again puts it back to false before the load runs, so a revisit shows the skeleton and not the last visit's content |
| 1.787 | Where a load is registered decides whether it owns the error page | a section's load that refuses puts its message in ui_failed and is not marked loaded, and neither the error page nor a redirect follows: the page stays, the section reports in place |

**Scenario range:** 1.786 – 1.787. **Test file:** `lex/test_project/tests/init/test_1cn_ui_loaded.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cn](batches.md).

---

### 1co. lex.ui pages: parts, translated sentences, text / fact / status / message ✅

**What it tests:** A page is made of parts that only describe what is on it; lex-app builds the components, passes the author's sentence to RFDS's translation as both id and default, and refuses anything outside the vocabulary with a message that says what to write (spec 6.5, 6.8).

**Why a regression matters:** a raw component slipping into a slot, an untranslated label, or an error that does not say what to do.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.788 | The sentence is the message | each sentence reaches RFDS's translation as both the id and the English default, with its values; a state value is shown as it is, untranslated; a tone outside the five is refused when the part is declared |
| 1.789 | A value says what it is and who sees it | each kind goes through RFDS's own formatter for it; a whole number is shown without decimals and any other with two; money takes a currency; an unknown kind, and a currency without kind="money", are refused when the fact is declared. visible= takes a permission (RFDS's check, with the string a page's requires= gives), a list of them (all must hold), ui.signed_in (always shown), or a state value; anything else, and an empty list, is refused with what to write. No component of the vocabulary takes a styling argument |
| 1.790 | A slot names what it refuses and what to write | the error names the page and the slot, says what it got, and says what to write instead; a single part and a list are both accepted; nothing gives an empty list |

**Scenario range:** 1.788 – 1.790. **Test file:** `lex/test_project/tests/init/test_1co_ui_page_parts.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1co](batches.md).

---

### 1cp. lex.ui pages: links by page function; ui.link and ui.button ✅

**What it tests:** A link points at a page function, never a route string; its parameters are checked against the page's route and declared query parameters when the page is built, and the error names both pages. A button that opens a page follows the same rule, and an outside site is the only place an address is written out (spec 6.7).

**Why a regression matters:** a link that builds a wrong address without complaint, or an in-app link that opens a new tab.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.791 | An address is built from the page and its parameters | fixed values give a plain address; state values give an expression that joins them into it (a number is turned into text); a missing or unknown parameter is an error naming the linking page, the target page, its route and what it takes; a function that is not a page is refused with what to pass instead; a table's row link is the route with its one argument replaced by the row's key |
| 1.792 | A link leads to a page, a record or an outside site | to= stays in the app; record= opens lex-app's own page of that record; href= takes only a full outside address and opens it as external; exactly one of the three is required; among actions the same link is drawn as a button |
| 1.793 | A button runs a handler or opens a page | a handler button calls the handler on click; a to= button goes to the page's address; both or neither is refused; tone="danger" gives RFDS's danger intent; the first action of a page is the primary one; a menu entry carries the translated sentence and the same click |

**Scenario range:** 1.791 – 1.793. **Test file:** `lex/test_project/tests/init/test_1cp_ui_page_links.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cp](batches.md).

---

### 1cq. lex.ui pages: sections with their own loading and failure; each, switch, custom_block ✅

**What it tests:** The author never writes a loading, failed or empty branch: a section with its own load shows a skeleton until that load has finished and its failure in place, and `ui.each` and `ui.switch` cover repetition and choice, while anything else goes through `ui.custom_block`, which must say why (spec 6.4, 6.6).

**Why a regression matters:** content shown before its data, a failed section that leaves a blank, or raw components entering a page unmarked.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.794 | A section with a load shows its skeleton, then its content or its failure | without a load the section is its content; with one, the content is shown only once ui.loaded says so, before that the failure message when there is one, else a skeleton (a table's, when the section is one table); on a page nothing is mounted (the opener starts the load); on a dashboard the load starts when the section mounts; a section's title is a sentence and comes first. Facts that follow one another form one key-value list; a section inside a section, and empty= without a table, are refused |
| 1.795 | each repeats and switch chooses | each item is rendered through the part the function returns; a function that returns a raw component is refused with what to return; switch shows the case matching the value and nothing otherwise; a case that is not a lex component is refused |
| 1.796 | Anything else goes through the marked way out | directly a raw component is refused, with the page, the slot and what to write; through ui.custom_block with a reason it is accepted and built as it is, and an error names it by its public name; without a reason, or with something that is not a component, custom_block is refused. A section hands its empty= sentence to a copy of its table, never to the author's part |

**Scenario range:** 1.794 – 1.796. **Test file:** `lex/test_project/tests/init/test_1cq_ui_page_sections.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cq](batches.md).

---

### 1cr. lex.ui pages: ui.table over a model; rows that open a page ✅

**What it tests:** `ui.table(Model)` is lex-app's server-paged table through the API as the user, the same component and look everywhere, with the columns named by field, a row that opens a page named by its function, and a sentence for "nothing here" (spec 6.5, 6.7).

**Why a regression matters:** a table that reads all rows into the browser, a row link written as a string, or an empty table with no words.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.797 | ui.table(Model) is the server-paged table | it is RFDS's data table over lex-app's paged table state for that model, showing the named columns under the model's own field labels; something that is neither a model nor a @ui.rows handler is refused with what to pass |
| 1.798 | Rows open a page, an empty table says so, a column can be relabelled | each row links to the page's address with the row's key in place of the page's argument; the empty sentence is translated and shown by RFDS's empty state; the column carries the given label; open= a page without exactly one argument is refused |

**Scenario range:** 1.797 – 1.798. **Test file:** `lex/test_project/tests/init/test_1cr_ui_page_tables.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cr](batches.md).

---

### 1cs. lex.ui pages: the record and list kinds ✅

**What it tests:** A page kind fixes where everything sits, on RFDS's own floorplan for that shape (the object page for one record, the list report for finding things), shows a skeleton until the page's load has finished, places the actions as RFDS's page header does, and refuses what does not belong in a slot (spec 6.3, 6.4).

**Why a regression matters:** a page that renders without its data, a slot that accepts anything, or actions in the wrong place.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.799 | A record page places status, facts and sections and waits for its load | it is RFDS's object page: the title in the page header, the status and the facts in the header area, each section under its translated heading; the whole page sits behind the load's loaded check, with a skeleton and the failure in place before it; the kind reports its page load and its sections' loads separately; tabs=True with two sections uses RFDS's tab navigation |
| 1.800 | A record page refuses what does not belong | each is a PageError naming the page and the slot and saying what to write -- also two sections with one title, a section hidden by a state value, a blank title, a title that is not words, two badges, and an action called Save (one fact or one section given without brackets is a list of one); a limit of RFDS's own (five tables on one page) names the page and quotes RFDS's sentence |
| 1.801 | Actions are placed as the page header places them | the first action is the primary button -- unless it is dangerous, which is never primary; exactly one further action is a button; two or more further actions are entries of the "More actions" menu, with their translated sentences and their tone; a primary action with visible= stays primary; the author's part is not changed; something that is not a button or a link is refused |
| 1.802 | A list page is a title, a table and its actions | it is RFDS's list report with the translated title, the table and the primary action; a table slot holding anything but a ui.table is refused |

**Scenario range:** 1.799 – 1.802. **Test file:** `lex/test_project/tests/init/test_1cs_ui_page_kinds.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cs](batches.md).

---

### 1ct. lex.ui pages: @ui.page, @ui.dashboard, registration once the module is loaded ✅

**What it tests:** A page function returns a page kind and may name pages declared below it, so it is not called when decorated; once the project's module is loaded, lex-app hands every declared page its chrome, its navigation entry, its permission and its loads, and `@ui.dashboard` builds the kind and starts its loads on mount (spec 6.2).

**Why a regression matters:** a page that cannot link forward, a page whose loads never start, or a navigation entry without words.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.803 | A page is declared without being called and may link forward | decorating calls neither function and returns it unchanged, with what a link needs (route, its arguments, its declared query parameters); registration builds each page in lex-app's chrome under its route, with the permission from requires= as RFDS's permission string and the opener last in its load events. A route that does not start with "/", a requires= that is not one permission, a params= given as one string, and a second function at a route are refused when the page is declared |
| 1.804 | Registration hands over the navigation entry and the loads | the navigation entry carries the kind's title; the kind's load is registered as the page's required load and the section's load as an optional one; registering twice adds nothing; a navigation page without a fixed title, a load= that is not a @ui.load handler, one load given twice, a page function that raises, a component refusing its arguments, something that is not a section in sections=, a function without return, and a function that does not return a kind, are each refused with the page named |
| 1.805 | A dashboard starts its loads on mount and create_app registers the pages | the dashboard returns the built page, a component, whose load starts when it mounts (a dashboard has no opener); it is not a link target; on a model it sits under @classmethod and receives the model; two of its loads on one state are refused, since each clears its state as it starts; create_app loads the project's module, then registers the declared pages, before it adds the home page |

**Scenario range:** 1.803 – 1.805. **Test file:** `lex/test_project/tests/init/test_1ct_ui_page_declare.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1ct](batches.md).

---

### 1cu. lex.ui pages: the namespace; a list and a record page end to end ✅

**What it tests:** A project writes `from lex import ui` and finds the page decorators, the kinds and the vocabulary beside the handler layer, and nothing that takes a styling argument; a list page that opens a record page builds, registers and loads exactly as a project's would (spec 6.1, 6.5).

**Why a regression matters:** a name missing from the namespace, a styling argument creeping into the vocabulary, or a page that builds but never loads.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.806 | One import gives the page kinds and the vocabulary | each name exists and is listed in __all__ -- except ui.list, the page kind, which a star import must not put in place of Python's list; no component or kind takes a styling argument |
| 1.807 | A list and a record page build, register and load | both build without one raw component in the page functions; the list links its rows to the record page; opening the record page runs the page load and then the section's load, both end loaded, and the state holds what they read |

**Scenario range:** 1.806 – 1.807. **Test file:** `lex/test_project/tests/init/test_1cu_ui_pages_namespace.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cu](batches.md).

### 1cv. lex.ui tables: @ui.rows, a handler that returns a table's rows; ui.address; ui.refresh ✅

**What it tests:** A table over rows that are not a lex model gets them from a plain function on the page's state, read as the user on a read-only copy of the state; a row that leads to a page of its own carries an address built in Python; another state's handler makes a table read again with `ui.refresh` (spec 5.1, 5.4, 5.6).

**Why a regression matters:** a rows handler that changes the state it reads, runs without its declared permission, returns something a table cannot show without saying so, or a link built from a string in the data.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.808 | A rows handler is a plain function that tables read | a plain function with no argument, or with `search` alone, is a rows handler; a generator, an async function and any other argument are refused with what to write; the browser cannot send it as an event; `lex reflex check` accepts it |
| 1.809 | Rows are read as the user on a read only copy | the rows carry the user and the field; lex-app searches and filters a handler without `search`, and a handler with `search` gets the text; writing a field fails the read and leaves the field as it was; a dict is refused with what to return; a missing permission refuses the read (a PermissionError in a test); given the table, the rows come back as its browser gets them |
| 1.810 | An address is built in python from a page | the argument and the query value are encoded into the page's route; an empty key gives no address; a value the page does not take, a missing argument, and something that is not a page are refused |
| 1.811 | Refresh names rows handlers and becomes their tables events | the result is accepted beside ui.toast; it becomes the event that makes that table read again, and is itself that list of events for a raw Reflex handler; something that is not a rows handler is refused |

**Scenario range:** 1.808 – 1.811. **Test file:** `lex/test_project/tests/init/test_1cv_ui_rows_handler.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cv](batches.md).

### 1cw. lex.ui tables: a table over a @ui.rows handler, end to end ✅

**What it tests:** lex-app generates the table's state; it reads every row as the user, searches, filters, sorts and pages them on the server, keeps only the visible page of the used fields in the browser's state, reads again after a handler of the same state, on `ui.refresh` and when its page opens again, totals per currency and exports what it shows (spec 5.5-5.8, 5.10).

**Why a regression matters:** a field the table does not show reaching the browser, a link built from data, a table that keeps showing what a handler has just changed, or a broken handler that breaks the page instead of the table.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.812 | The browser gets one page of the fields the table uses | its state holds the rows with only the columns' fields, the currency field, the key and the link field; the e-mail address stays on the server; the plain string is no link; the table's state is reachable for later reads |
| 1.813 | Search filters and sort run on the server | a search finds a row by its tag; each filter keeps the matching rows; the filter choices are the column's values after "All"; the first sort of a money column is high to low; clearing the filters clears the dropdowns; a filter the browser sends on a field the table does not show is refused |
| 1.814 | Totals per currency and export of what is shown | there is one totals row per currency over all matching rows; the export has the columns' labels and each amount's currency, the matching rows only, and tags joined; each exported column keeps its own cell type |
| 1.815 | A handler of the state or ui refresh makes the table read again | after each the table shows the new rows without being asked; a table that is not on screen does not read |
| 1.816 | Opening the page again and a list pages search in the address | the opener tells the page's table to read its first page again (not a table that appears only after a load, which reads when it appears); the list page's table starts with the address's search, and typing a search writes it back |
| 1.817 | A broken rows handler breaks only its table | a refusal shows its own words in the table; a failure shows a message with a reference and never its detail; a filter of more than 50 values is the table's error too (the advice to search goes to the log, with the reference) |

**Scenario range:** 1.812 – 1.817. **Test file:** `lex/test_project/tests/init/test_1cw_ui_rows_table.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cw](batches.md).

### 1cx. lex.ui tables: what a column and a table refuse; a model table narrowed with where= ✅

**What it tests:** Everything about a table that can be checked before a row is read stops the app from starting with the page, the slot and what to write; a model table is narrowed with `where=` and read through lex-app's API (spec 5.2, 5.3, 5.11).

**Why a regression matters:** a table that breaks only in the browser, a column kind drawn differently from page to page, or a model table that cannot be narrowed without leaving the API.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.818 | A column says what it is and is checked when declared | each kind is accepted; a currency code and a currency field are told apart; tones go with status only; show= and kind= are not given together; a link field must be a field name; a wrong kind names the kinds |
| 1.819 | A table declared wrongly names the page the slot and what to write | each is a PageError naming the page and the slot, saying what it got and what to write |
| 1.820 | A model table is narrowed with where and searched through the api | the table's state receives the where= values when it appears (a state value included); the toolbar has a search box; a filter offers the field's choices after "All"; two tables narrowed differently never share a state; a column keeps its field's kind; the field a row button reads reaches the browser; building the same table twice finds one state class; its state takes only its where= keys and its own filters, while lex_table's own state still filters on any field; search covers a shown text column only |

**Scenario range:** 1.818 – 1.820. **Test file:** `lex/test_project/tests/init/test_1cx_ui_table_checks.py`. **Type:** U. **Status:** ✅ Complete (2026-11-23). Batch: [1cx](batches.md).

### 1cy. lex.ui adoption: a lex.ui part inside a raw page; the user's permissions in a body ✅

**What it tests:** A project written in raw Reflex moves to lex.ui one table at a time (`ui.embed`), and a handler body reaches the user's Keycloak permission list for the project's own logic (`self.user.permissions`).

**Why a regression matters:** a project that must convert a whole page before it can use one `@ui.rows` table, or a handler that cannot call the project's own logic.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.821 | A lex ui table sits inside a raw page | the table is RFDS's data table over its generated state, inside the raw page; a whole page kind and something that is not a lex.ui part are refused with what to write; a mistake in the embedded part names it by the name given |
| 1.822 | A handler body reads the users permission list | both see the user's Keycloak permissions as Keycloak lists them; outside a handler body reading them is refused |

**Scenario range:** 1.821 – 1.822. **Test file:** `lex/test_project/tests/init/test_1cy_ui_adoption.py`. **Type:** I. **Status:** ✅ Complete (2026-11-23). Batch: [1cy](batches.md).

### 1cz. lex.ui tables: a table that leaves the screen keeps none of its rows ✅

**What it tests:** A table's rows - often people, read for one user from another system - stay in the tab's state only while the table is on screen; once it leaves, its page, count, totals and filter choices are gone.

**Why a regression matters:** a person's data left in a state nobody looks at any more, which Reflex keeps for an hour and pickles to Redis with two workers.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.823 | A table that leaves the screen keeps none of its rows | once it has left, its state holds no row, no count, no totals and no filter choice; when it appears again it reads its rows afresh |

**Scenario range:** 1.823. **Test file:** `lex/test_project/tests/init/test_1cz_ui_table_close.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1cz](batches.md).

### 1da. lex.ui links: a raw Reflex page as somewhere a link may lead ✅

**What it tests:** A project moving to lex.ui a table at a time keeps its pages in raw Reflex for a while; `ui.reflex_page("/route")` declares one as a link target, so `ui.address`, a table's `open=`, `ui.link(to=)` and `ui.button(to=)` lead to it as to a `@ui.page`.

**Why a regression matters:** a converted table whose rows can no longer be opened (or opened in a new tab), or a row link built from a string.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.824 | A raw Reflex page is somewhere a link may lead | the address is the route with its value encoded as one piece, and is the kind of address a table follows; the table's rows link by their key; the link and the button lead there; a missing or unknown value, and a route that is no route, are refused with what to write; an app without a declared page does not start, naming the route |

**Scenario range:** 1.824. **Test file:** `lex/test_project/tests/init/test_1da_ui_reflex_page.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1da](batches.md).

### 1db. lex.ui's part of lex-app's app, and every page in lex-app's chrome ✅

**What it tests:** `create_app` hands lex.ui's pages to the app in one internal step (the declared `@ui.page` pages, `/ui-error`, the jobs' clock, the route check), and every `@ui.page` is in lex-app's chrome after the user's grants. lex.ui has no hook for a project's own frame or gate (the 2026-11-24 `ui.chrome`/`ui.install` was withdrawn after review).

**Why a regression matters:** a page registered twice, without its error page, or outside lex-app's chrome.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.825 | Create app hands lex ui its pages in one step | the declared page is registered once; the error page is added after the user's grants; the jobs' clock is registered once; the routes are checked after the pages; create_app runs the step, and lex.ui offers no app or chrome hook |
| 1.826 | Every page is in lex apps chrome after the grants | a page and the error page are in lex-app's chrome; the page's load events are the grants, then the opener - nothing of the project's between them |

**Scenario range:** 1.825 – 1.826. **Test file:** `lex/test_project/tests/init/test_1db_ui_install.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1db](batches.md).

### 1dc. lex.ui handlers declared on a state mixin are each state's own ✅

**What it tests:** A handler shared through a Reflex state mixin keeps its running status and its repeat guard per state that uses the mixin.

**Why a regression matters:** a button that stays busy, or a press that silently does nothing, because another state is running "the same" handler.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.827 | A handler from a mixin is each states own | each state keeps the handler under its own name, so a view of one state's running export does not show the other's; the second export runs too instead of being ignored as a repeat; a handler written on the state itself keeps its name |

**Scenario range:** 1.827. **Test file:** `lex/test_project/tests/init/test_1dc_ui_mixin_handlers.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dc](batches.md).

### 1dd. lex.ui tables: a date column is filtered by a range of days ✅

**What it tests:** `filters=` names columns; a `date` or `datetime` column of a `@ui.rows` table is filtered by a range of days, any other by a dropdown of its values - on the server, over every row, only on the fields the table offers.

**Why a regression matters:** a list that cannot be narrowed to a month, a range that drops its last day, or a filter on a field the table does not offer.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.828 | A date column is filtered by a range of days | the kind column has a dropdown and the date column a date-range picker; a month keeps its first to last day (a datetime on the last evening included, an undated row never); an open start or end; the range and the dropdown together; clearing empties the picker; a range for a field that is no date filter and a date that is not a day change nothing |

**Scenario range:** 1.828. **Test file:** `lex/test_project/tests/init/test_1dd_ui_table_filters.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dd](batches.md).

### 1de. lex.ui handlers may hand on to another state's handler ✅

**What it tests:** A handler body returns or yields another state's handler among its lex.ui results; the browser runs it in order. Reflex's own raw events stay in the custom region.

**Why a regression matters:** a handler that cannot chain to another state, a result order the browser does not keep, or a browser script slipping out of an ordinary handler.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.829 | A handler hands on to another states handler | the other state's handlers are results like the toast, in the order written, and reach the browser as they are; a browser script, a redirect and a plain object are refused with the handler's name and where raw events go |

**Scenario range:** 1.829. **Test file:** `lex/test_project/tests/init/test_1de_ui_reflex_events.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1de](batches.md).

### 1df. lex.ui: a button that says why it cannot be pressed; a download the browser can place ✅

**What it tests:** `ui.button(disabled=<reason or row field>)` keeps a blocked button in place with its reason; `ui.download` tells the browser what a file built from data is, from its name.

**Why a regression matters:** a button that vanishes or stays pressable for a row it cannot act on, a yes/no value shown as a reason, or a PDF the browser treats as text.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.830 | A button says why it cannot be pressed | each row's button is blocked exactly when its row has a reason, stays focusable and shows it; a fixed reason is shown; a yes/no field and True are refused with what to give instead |
| 1.831 | A download tells the browser what the file is | a file built from data carries the media type its name says; a download by URL carries none |

**Scenario range:** 1.830 – 1.831. **Test file:** `lex/test_project/tests/init/test_1df_ui_button_disabled.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1df](batches.md).

### 1dh. lex.ui forms: what was entered reaches the handler as Python values, or as a message at its field ✅

**What it tests:** a field's value turned into its kind's Python value, or a sentence for its field; one set of fields per handler; `ui.fact` takes the form kinds.

**Why a regression matters:** a text where a number was promised, a wrong value reaching the handler's body, a message nobody can act on, or one handler silently receiving two sets of fields.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.833 | Each kind reaches the handler as its Python value | text stripped; number int or Decimal; money and percent Decimal; date; date and time in the app's zone; year int; box bool; choice text; empties are "", False or None; undeclared fields dropped |
| 1.834 | A wrong or missing value is a message for its field | required, not a number, NaN, infinity, not a date, not four digits, a choice outside its options (fixed or a state field) each have their sentence |
| 1.835 | A handler receives one set of fields | the same fields again are accepted; others are refused naming the handler and where the first set was declared |
| 1.836 | A fact shows the kinds a field takes | date formatted by RFDS, year without separator, bool as Yes/No, choice as it is; an unknown kind lists the kinds |

**Scenario range:** 1.833 – 1.836. **Test file:** `lex/test_project/tests/init/test_1dh_ui_form_values.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dh](batches.md).

### 1di. lex.ui forms: the handler runs once, with checked values; the page follows what it did ✅

**What it tests:** through Reflex's own event processor: values checked before the body, the result flag, a job's progress, a wizard's steps, a new visit.

**Why a regression matters:** a body that runs on unchecked values, a result that outlives the input it belongs to, or a wizard that runs its handler once per step.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.837 | The values are checked before the handlers body runs | with problems the body does not run and each field has its message; filled in, it runs once with a Decimal and the result flag is set |
| 1.838 | A refusal and a failure are shown and no result appears | a refusal in its own words, a failure with a reference, neither sets the result; sending again takes the old result away, also when refused at a field |
| 1.839 | A job reports its progress and then its result | an empty year does not start the job; progress before the result; rows stay on the server; a refused job shows no result |
| 1.840 | A wizard sends one form and runs its handler once | an empty step stays with its message; a filled one moves on; Back; only the last step runs the body with every value; an earlier problem returns there |
| 1.841 | A new visit starts the form over | the opener clears messages, steps, results and an open dialog |

**Scenario range:** 1.837 – 1.841. **Test file:** `lex/test_project/tests/init/test_1di_ui_form_submit.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1di](batches.md).

### 1dj. lex.ui pages: the form kind -- fields, one submit, a result; a wizard; a tool ✅

**What it tests:** the form page built from RFDS's page header, form and inputs; a tool with a job and a result; a wizard; load= and @ui.page; every build-time error.

**Why a regression matters:** a field sent while the user types, a submit button that can be pressed twice, a result shown before there is one, or an error that does not say the page, the slot and what to write.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.842 | A form page draws each field and sends the form once | RFDS's input per kind, labels, help, required mark, the server's message per field, dropdown or type-ahead, the submit button after the handler showing it running; one event: the submit |
| 1.843 | A tool is a form with a result | a job's progress and Stop; the result behind its flag, full width, with its table, section and download; one primary button |
| 1.844 | Steps make the same form a wizard | RFDS's stepper, one form with the other steps hidden, Continue then the handler's name, Back |
| 1.845 | A form waits for its load and is a page like any other | registered with its load; skeleton until loaded; a field starts from the state; a result section's load is a part load |
| 1.846 | A form declared wrongly says the page, the slot and what to write | twenty refusals read word for word: key twice, unknown kind, file, options misplaced or wrong, other state, currency, fields/steps, submit, values argument, a handler with other fields; a field in a section |

**Scenario range:** 1.842 – 1.846. **Test file:** `lex/test_project/tests/init/test_1dj_ui_form_page.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dj](batches.md).

### 1dk. lex.ui: a button that asks first -- confirm=, ask= -- and that shows its handler running ✅

**What it tests:** RFDS's confirmation and dialog opened from a button, one per button as written; a dangerous button must ask; ask= values checked like a form's; every button shows its handler running.

**Why a regression matters:** a dangerous action that runs on one click, a dialog per table row, a reason reaching the handler unchecked, or a button pressable again while its handler runs.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.847 | Confirm asks before the handler runs | the button opens RFDS's confirmation beside the page; agreeing closes it, then runs the handler; vague questions refused |
| 1.848 | A dangerous button must ask | refused as a button, a menu entry and a row action, with what to write; one dialog per table, with the row's key |
| 1.849 | Ask opens a small dialog and hands its values to the handler | RFDS's dialog with RFDS's form; empty: stays open with the message; filled: closes and the body gets the key and the values; wrong handler or fields refused |
| 1.850 | A button shows its handler running | action and job buttons load while running; a row's button only for its key; a raw handler or a page link not |
| 1.851 | The dialog is opened and closed on the server | asking records the dialog and values; dismissing forgets them |

**Scenario range:** 1.847 – 1.851. **Test file:** `lex/test_project/tests/init/test_1dk_ui_button_confirm_ask.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dk](batches.md).

### 1dl. lex.ui forms: a field where the user chooses several of a list of options ✅

**What it tests:** `ui.field(kind="choices", options=...)` -- RFDS's checkbox group or multi-select, a `list[str]` in the order of the options checked on the server, in a form, a step and a button's ask=; `ui.fact` shows the kind.

**Why a regression matters:** a text where a list was promised, a value that is no option reaching the body, a required field passing with nothing ticked, or a field that calls the server on every tick.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.852 | Several choices reach the handler as a list of the options | a list of texts in the options' order, each once; one value alone, `[]`, `None`, `""`, `False`; required: at least one; no option, or not texts: refused; options from the state |
| 1.853 | The field is RFDS's input for several and sends nothing while picking | checkbox group up to 12 fixed options, multi-select above or from the state; label, help, required mark, the server's message; `value=` a list or a state value; one event: the submit; compiled with Reflex's page compiler; in a step and in ask= |
| 1.854 | A run hands the body the checked list | through Reflex's event processor and `ui.testing.run`: the list in option order; nothing ticked or no option is a message at the field and no run; a wizard's last step; a dialog's row key and list |
| 1.855 | Several choices declared wrongly, and shown | no options, non-text options, a start that is no list, options on another kind, the kinds listed; `ui.fact(kind="choices")` joined by commas |

**Scenario range:** 1.852 – 1.855. **Test file:** `lex/test_project/tests/init/test_1dl_ui_form_choices.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dl](batches.md).

### 1dy. lex.ui tables: the name pinned and given room, a code kept narrow, the Columns menu, every row at once, totals that say what they sum ✅

**What it tests:** `ui.column(kind="name"|"code", pin=True)`, `ui.table(choose_columns=True, page_size=N|None, totals={...: "sum of all rows"})` -- RFDS's sticky column, field widths, column chooser, pagination and totals, on rows and model tables.

**Why a regression matters:** a name that scrolls away, a user's column choice that hides the row's name, a row beyond 200 out of reach, or a total whose meaning changes silently when the user searches.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.940 | The name stays in place and has room; a code stays narrow | sticky start, entity kind at the wide width, identifier at the narrow width; model fields too |
| 1.941 | A pin written wrongly says what to write | not a bool; not the first column; no width argument |
| 1.942 | The user chooses, orders and sizes the columns | RFDS's column chooser; the first column always shown and first; widths 40-1,200 px |
| 1.943 | `page_size=` sets the rows a page, `None` shows every row | 50; default 25; `None`: 200, no size picker, pagination past 200; wrong sizes refused |
| 1.944 | Every row in one scroll, and past 200 the rest is reached | 150 on one page; 250 as 200 + 50 |
| 1.945 | A totals row says what it sums and can sum every row | "Total", "Total of matching rows", "Total of all rows"; mixing refused |
| 1.946 | A table with all of it compiles | Reflex's page compiler |

**Scenario range:** 1.940 – 1.946. **Test file:** `lex/test_project/tests/init/test_1dy_ui_table_columns_rows.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dy](batches.md).

### 1dz. lex.ui pages: a section closed until the user opens it, saying how many rows it holds ✅

**What it tests:** `ui.section(title, ..., collapsed=True)` -- RFDS's section as a native disclosure, closed at first, its title counting its table's rows; in a form's `result=` and `ui.embed`; refused on a record page.

**Why a regression matters:** a detail that shows open and buries the result, a count that is wrong or shows before the rows are read, or a closed section where RFDS cannot close one.

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.947 | A closed section opens in the browser and says how many rows it holds | RFDS's collapsible section, closed; the count once loaded; no table: title alone; model tables; embed |
| 1.948 | A closed section compiles | Reflex's page compiler |
| 1.949 | A closed section where RFDS cannot close one is refused | record page; not a bool |

**Scenario range:** 1.947 – 1.949. **Test file:** `lex/test_project/tests/init/test_1dz_ui_section_collapsed.py`. **Type:** U. **Status:** ✅ Complete (2026-11-24). Batch: [1dz](batches.md).
