---
title: "Environment Variables"
---

Lex App reads its runtime configuration from environment variables — usually loaded from the `.env` file `lex setup` generates at your project root. This page lists the variables the framework actively reads.

> [!note]
> This index covers the variables the framework reads directly. Your project's Django settings may layer additional ones on top. If a variable isn't listed here, check `lex_app/settings.py` in the installed package.

## Timezone

| Variable        | Purpose                                                                                                                                                                                                         |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LEX_TIME_ZONE` | IANA timezone used as the display and naive-input zone (e.g. `Europe/Berlin`, `America/New_York`). Storage is always UTC — this controls how the server renders datetimes and interprets naive user input. Default `Europe/Berlin`. |

## Async / Celery

| Variable               | Purpose                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `CELERY_ACTIVE`        | `true` to let the framework dispatch calculations to Celery workers when they're available. `@lex_shared_task` still works, but root `CalculationModel` runs no longer require it just to use Celery. Otherwise tasks run synchronously in the current process. See [[calculations/celery and async calculations]]. |
| `IS_RUNNING_IN_CELERY` | Set to `true` inside Celery worker processes so the framework knows it's executing a queued task rather than a web request. Set automatically when you launch via `lex celery` / `lex celery-workers`; if you run a standalone recovery worker such as `lex-recovery-beat`, export it there too. |

## Calculation threading

These control the thread pools that keep long-running calculations from blocking the web server. Defaults are sensible — only tune them if you have a specific throughput or responsiveness problem.

| Variable                  | Purpose                                                                                       |
| ------------------------- | --------------------------------------------------------------------------------------------- |
| `LEX_CALCULATION_THREADS` | Size of the dedicated pool that runs in-process calculations off the request thread, so calculations never starve API calls, WebSocket auth, or health checks. Default `10`. |
| `ASGI_THREADS`            | Size of the ASGI sync executor used for regular sync work. Raising it gives the server more headroom for concurrent sync operations. Default `3`. |

## Worker recovery & shutdown

These govern how the framework recovers tasks from dead workers and how idle workers shut themselves down. Defaults are production-ready; the idle-shutdown knobs only take effect in a non-local `DEPLOYMENT_TARGET`. See [[calculations/celery and async calculations]] for the full picture.

| Variable                          | Purpose                                                                                       |
| --------------------------------- | --------------------------------------------------------------------------------------------- |
| `LEX_TASK_RECOVERY_ENABLED`       | Master switch for the heartbeat/dead-worker recovery system. Default `false` — turn it on only in deployments where you also run `lex-recovery-supervisor` or `lex-recovery-beat`. |
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

| Variable                                  | Purpose                                                                                  |
| ----------------------------------------- | ---------------------------------------------------------------------------------------- |
| `IS_STREAMLIT_ENABLED`                    | `true` to enable the Streamlit toolbar icon in the frontend. See [[access-and-dashboards/streamlit dashboards]]. |
| `STREAMLIT_URL` / `BASE_URL`              | Public URL used by the embedded dashboard proxy. When this is HTTPS, Lex App defaults to secure cross-site cookies for the iframe. |
| `LEX_PROXY_PORT`                          | Port exposed by the local Streamlit proxy when running `lex streamlit`. Default `8501`. |
| `LEX_PROXY_INTERNAL_URL`                  | Full base URL the dashboard uses to reach the proxy when it is not `http://127.0.0.1:$LEX_PROXY_PORT`. |
| `UPSTREAM` / `STREAMLIT_UPSTREAM`         | Internal Streamlit server URL behind the proxy. Default `http://localhost:8080`. |
| `UPSTREAM_TIMEOUT_SECONDS`                | Timeout for proxy requests to Streamlit. Default `30`. |
| `SESSION_SECRET`                          | Signing key for dashboard session cookies. Optional: when unset, the key is derived from `DJANGO_SECRET_KEY`, which every deployment already has and which is stable across restarts and identical on every replica. `SESSION_KEY` and `SESSION_SECRET_KEY` are accepted aliases. |
| `SESSION_SAMESITE`                        | Cookie SameSite mode for the dashboard proxy: `none`, `lax`, or `strict`. Defaults to `none` on HTTPS and `lax` otherwise. |
| `SESSION_HTTPS_ONLY`                      | Whether dashboard cookies are marked `Secure`. Defaults to `true` for HTTPS public URLs. Required when `SESSION_SAMESITE=none`. |
| `TOKEN_REDIS_URL` / `REDIS_URL`           | Shared token store for dashboard sessions. Use this when running more than one proxy replica, or when you want sessions to survive proxy restarts. |
| `LEX_PROXY_REPLICAS`                      | Number of Streamlit proxy replicas. When greater than `1`, Lex App requires a shared Redis token store instead of process-local memory. |
| `LEX_STREAMLIT_DISCONNECTED_SESSION_TTL`  | How long Streamlit keeps a disconnected session around for reconnects. Default `600` seconds. |
| `LEX_INTERNAL_AUTH_SECRET`                | Shared secret for the proxy-to-Streamlit token refresh channel. `lex streamlit` sets this automatically; set it yourself only when running the two processes separately. |
| `REACT_APP_URL` / `LEX_FRONTEND_URL`      | Optional origin allowed to hand the proxy a renewed dashboard token. Normally derived from `DOMAIN_HOSTED`; set one only when the frontend is served from a different host. |
| `STRIP_AUTH_TOKEN_FROM_URL`               | `true` to redirect the dashboard's first request to the same URL without its `auth_token`. Default `true`. |
| `STATIC_ASSET_MAX_AGE`                    | `max-age` for Streamlit package assets served by the proxy. Default one year. |
| `STATIC_GZIP_MIN_SIZE` / `STATIC_GZIP_LEVEL` | Compression floor and zlib level for Streamlit assets served by the proxy. Defaults `500` and `6`. |
| `JWKS_CACHE_TTL` / `JWKS_RETRY_BACKOFF_SECONDS` | How long Keycloak signing keys are cached (default `3600`), and how long to wait before retrying a failed refresh while continuing to serve cached keys (default `30`). |
| `LEX_THEME_FOLLOW`      | Keep embedded Streamlit pages in the same light/dark mode as Lex App. Enabled by default; set to `0`, `false`, `no`, or `off` to let Streamlit control its own theme. |

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

| Variable                        | Default | Purpose                                                                                       |
| ------------------------------- | ------- | --------------------------------------------------------------------------------------------- |
| `LOG_LEVEL`                     | `INFO`  | Application-wide log level. Raising it to `DEBUG` turns on debug output everywhere — including third-party libraries — so the console gets noisy. Use it when you want *everything*. |
| `LEX_LOG_LEVEL`                 | `INFO`  | Log level for the **Lex framework only** (`lex.*` loggers). Set it to `DEBUG` to see the framework's own debug output without the third-party noise `LOG_LEVEL=DEBUG` would pull in. |
| `LEX_SUPPRESS_INSECURE_WARNING` | `True`  | Hides urllib3's `InsecureRequestWarning`, which otherwise prints on every request the framework makes to the auth host when TLS verification is off. Set it to `False` if you're debugging certificates and want the warning back. |
| `LEX_SUPPRESS_WARNINGS`         | `True`  | Quiets Python's warning system at startup (e.g. Django's "Accessing the database during app initialization" `RuntimeWarning`) so local logs stay clean. Set it to `False` to restore Python's default warning behaviour while debugging. |

> [!tip]
> `LEX_LOG_LEVEL` and `LOG_LEVEL` are independent. For day-to-day debugging of your own app and the framework, reach for `LEX_LOG_LEVEL=DEBUG` first — it keeps the console readable. Drop down to `LOG_LEVEL=DEBUG` only when you suspect the issue is in a third-party library.

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
- [[start-here/installation]] — how `.env` is generated by `lex setup`.
