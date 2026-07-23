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
