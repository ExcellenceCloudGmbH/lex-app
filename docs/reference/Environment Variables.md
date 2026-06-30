---
title: "Environment Variables"
---

Lex App reads its runtime configuration from environment variables — usually loaded from the `.env` file `lex setup` generates at your project root. This page lists the variables the framework actively reads.

> [!note]
> This index covers the variables the framework reads directly. Your project's Django settings may layer additional ones on top. If a variable isn't listed here, check `lex_app/settings.py` in the installed package.

## Async / Celery

| Variable               | Purpose                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `CELERY_ACTIVE`        | `true` to let the framework dispatch calculations to Celery workers when they're available. `@lex_shared_task` still works, but root `CalculationModel` runs no longer require it just to use Celery. Otherwise tasks run synchronously in the current process. See [[features/processing/celery and async calculations]]. |
| `IS_RUNNING_IN_CELERY` | Set to `true` inside Celery worker processes so the framework knows it's executing a queued task rather than a web request. Set automatically when you launch via `lex celery` / `lex celery-workers`; if you run a standalone recovery worker such as `lex-recovery-beat`, export it there too. |

## Calculation threading

These control the thread pools that keep long-running calculations from blocking the web server. Defaults are sensible — only tune them if you have a specific throughput or responsiveness problem.

| Variable                  | Purpose                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------- |
| `LEX_CALCULATION_THREADS` | Size of the dedicated pool that runs in-process calculations off the request thread, so calculations never starve API calls, WebSocket auth, or health checks. Default `10`. |
| `ASGI_THREADS`            | Size of the ASGI sync executor used for regular sync work. Raising it gives the server more headroom for concurrent sync operations. Default `3`. |

## Worker recovery & shutdown

These govern how the framework recovers tasks from dead workers and how idle workers shut themselves down. Defaults are production-ready; the idle-shutdown knobs only take effect in a non-local `DEPLOYMENT_TARGET`. See [[features/processing/celery and async calculations]] for the full picture.

| Variable                          | Purpose                                                                                       |
| --------------------------------- | --------------------------------------------------------------------------------------------- |
| `LEX_TASK_RECOVERY_ENABLED`       | Master switch for the heartbeat/dead-worker recovery system. Set `false` in local dev and CI where there's no real Redis-backed Celery. Default `true`. |
| `LEX_TASK_HEARTBEAT_INTERVAL`     | How often (seconds) a running task emits a liveness heartbeat. Default `5`. |
| `LEX_TASK_HB_TTL_MULTIPLIER`      | A task is considered dead after `HEARTBEAT_INTERVAL × TTL_MULTIPLIER` seconds without a heartbeat. Default `3`. |
| `LEX_TASK_SUPERVISOR_SCAN_INTERVAL` | How often (seconds) the supervisor sweeps for dead workers and requeues their tasks. Default `10`. |
| `LEX_TASK_MAX_RETRIES`            | Max automatic requeues after a dead-worker event before the task is marked failed. Default `4`. |
| `LEX_WORKER_IDLE_SHUTDOWN_ENABLED` | Master switch for all worker self-termination — idle watchdog, cancel fast-path, and post-task warm shutdown. Non-local `DEPLOYMENT_TARGET` only. Set `false` for long-lived `-B`/recovery-beat workers. Default `true`. |
| `LEX_WORKER_IDLE_SHUTDOWN_SECONDS` | Seconds a worker may sit with no work before the idle watchdog shuts it down. Default `30`. |
| `LEX_CLUSTER_CANCEL_ENABLED`       | Whether cancelling a calculation cascades to descendant tasks on other worker pods via the Redis cancel index. Inert when `CELERY_ACTIVE` is off or no Redis is reachable. Default `true`. |
| `LEX_CLUSTER_CANCEL_TREE_TTL_SECONDS` | TTL (seconds) for the Redis cancel-index tree mapping a calculation to its descendant task IDs. Default `14400` (4 h). |
| `LEX_CLUSTER_CANCEL_MARKER_TTL_SECONDS` | TTL (seconds) for the cooperative cancel marker a task checks to self-abort. Default `3600` (1 h). |

## Streamlit

| Variable                | Purpose                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `IS_STREAMLIT_ENABLED`  | `true` to enable the Streamlit toolbar icon in the frontend. See [[features/access-and-ui/streamlit dashboards]]. |

## Keycloak / OIDC

| Variable                | Purpose                                                                                   |
| ----------------------- | ----------------------------------------------------------------------------------------- |
| `KEYCLOAK_REALM`        | Name of the Keycloak realm the framework targets when syncing models, fields, and groups. |
| `KEYCLOAK_REALM_NAME`   | Display name of the realm (used during bootstrap). Falls back to `KEYCLOAK_REALM` if unset. |
| `OIDC_RP_CLIENT_ID`     | Your project's OIDC client ID — the identifier the browser logs in against.               |

Additional `KEYCLOAK_*` / `OIDC_*` variables (server URL, client secret, admin credentials) are read at the Django-settings layer. `lex setup` writes a complete set into your `.env` — start from that file rather than constructing the list by hand.

## Mail

| Variable            | Purpose                                                              |
| ------------------- | -------------------------------------------------------------------- |
| `SENDGRID_API_KEY`  | API key used to send the PDF test report (`lex pytest --report-and-email`) and any project-level transactional mail. |

## Widget integrations

| Variable                   | Purpose                                                                 |
| -------------------------- | ----------------------------------------------------------------------- |
| `QUACKBACK_WIDGET_SECRET`  | Shared secret used to sign the short-lived HS256 SSO token the frontend mints at `POST /api/quackback-widget-token` to identify the logged-in user to the embedded Quackback feedback widget. Leave unset to disable token minting. |

## Logging & warnings

| Variable                | Purpose                                                                                   |
| ----------------------- | ----------------------------------------------------------------------------------------- |
| `LEX_LOG_LEVEL`         | Log level for the framework's own `lex.*` loggers. Set `DEBUG` to surface the framework's debug output **without** turning on the third-party DEBUG firehose. Default `INFO`. |
| `LEX_SUPPRESS_INSECURE_WARNING` | Suppresses urllib3's `InsecureRequestWarning` (the Keycloak admin client can emit one per request against a self-signed dev endpoint). Set `False` to restore the warning while debugging TLS. Default `True`. |
| `LEX_SUPPRESS_WARNINGS` | Suppresses Python warnings raised during app startup (`AppConfig.ready()`). Set `False` to restore them. Default `True`. |

## Where these get set

| Place                 | When it's used                                              |
| --------------------- | ----------------------------------------------------------- |
| `.env` at project root | Local development. Loaded by PyCharm run configs and `set -a; source .env; set +a` in the terminal. |
| Container / cloud env | Production. Whatever your platform's secret manager exposes (Docker `--env-file`, Kubernetes Secrets, etc.). |

> [!tip]
> If you change anything in `.env`, restart your `lex start` / `lex streamlit` processes (and your Celery workers if you have them) — the variables are read once at startup.

## See also

- [[reference/CLI Commands]] — every command that reads these variables.
- [[reference/lex_config.md|lex_config.py]] — the Python-side configuration that complements these env vars.
- [[installation]] — how `.env` is generated by `lex setup`.

