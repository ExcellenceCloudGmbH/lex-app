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
| 1.366 | the compiled app | sign-in routes, guard before `resolve`, connection middleware, every dashboard |
| 1.367 | registration | the `Reflex` report and sidebar entry only when enabled; discovery never imports the Reflex package twice |
| 1.368 | granian's uvloop | `AppConfig.ready()` survives a loop `nest_asyncio` cannot patch |
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
