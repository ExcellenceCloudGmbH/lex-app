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
| `FLOWER_ADDRESS`       | Interface [Flower](https://flower.readthedocs.io/) binds to when you run `lex flower`. Default `127.0.0.1` — change it to `0.0.0.0` to reach the dashboard from outside the container. |
| `FLOWER_PORT`          | Port Flower listens on. Default `5555`. |
| `FLOWER_URL_PREFIX`    | Sub-path Flower is served under, when it sits behind a reverse proxy rather than at the root. Empty by default. |
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
| `KEYCLOAK_URL`          | Base URL of the Keycloak server, e.g. `https://auth.excellence-cloud.de`. Read by the admin client, the dashboard proxy and the frontend config endpoint. Required — there is no default. |
| `OIDC_RP_CLIENT_SECRET` | Client secret for the confidential OIDC client. Required for `lex init` to talk to the Keycloak admin API. |
| `OIDC_RP_CLIENT_UUID`   | Keycloak's *internal* UUID for that client — not the client ID. Used to address the client's authorization endpoints directly; `lex init --bootstrap` fills it in for you. |
| `KEYCLOAK_CLIENT_ID`    | Client ID handed to the browser at runtime (served as `REACT_APP_KEYCLOAK_CLIENT_ID`). Usually the same value as `OIDC_RP_CLIENT_ID`. |
| `KEYCLOAK_INTERNAL_CLIENT_ID` | Client ID used for server-to-server calls to the platform API, as opposed to the one the browser logs in with. |
| `KEYCLOAK_AUTHZ_REQUEST_TIMEOUT` | Timeout, in seconds, for calls to Keycloak's authorization endpoints. Raise it if `lex init` times out against a slow or distant realm. |
| `INSTANCE_CONTROLLER_BASE_URL` | Base URL of the instance-controller service that `lex init --bootstrap` opens in the browser. |
| `KEYCLOAK_SETUP_CALLBACK_URL` | URL that bootstrap flow calls back with the credentials it obtained. Both of these have working defaults — set them only against a self-hosted controller. |
| `OIDC_ISSUER`           | Expected `iss` claim when the dashboard proxy validates a token. Defaults to the realm URL derived from `KEYCLOAK_URL` and `KEYCLOAK_REALM`; set it only when the issuer the tokens carry differs from the URL you reach Keycloak on (a split-horizon DNS setup, typically). |

`lex setup` writes a starting set of these into `.env` and `lex init --bootstrap`
fills in the ones the browser flow can discover. Start from that file rather
than constructing the list by hand.

> [!warning] Four of these are load-bearing together
> OIDC is enabled only when `KEYCLOAK_URL`, `KEYCLOAK_REALM`, `OIDC_RP_CLIENT_ID`
> **and** `OIDC_RP_CLIENT_SECRET` are all set. Miss one and authentication is
> switched off silently — the app starts, serves pages, and never asks anyone to
> log in. If a deployment is unexpectedly open, check all four before anything
> else.

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

## Database

The app builds its `DATABASES["default"]` by picking one of several
preconfigured blocks. `DATABASE_DEPLOYMENT_TARGET` chooses the block; the rest
fill it in.

| Variable | Purpose |
|---|---|
| `DATABASE_DEPLOYMENT_TARGET` | Which connection profile to use: `local` (SQLite file, for a machine with no PostgreSQL), `default` (PostgreSQL on `localhost`), `GCP`, `DOCKER-COMPOSE`, or `K8S`. Default `default`. The three deployed profiles are identical apart from `K8S`, which disables TLS on the connection because the sidecar terminates it. |
| `DATABASE_NAME`   | Database name. Read by the `GCP`, `DOCKER-COMPOSE` and `K8S` profiles; the `default` profile derives the name from your repository name instead. |
| `DATABASE_DOMAIN` | Database host for those same three profiles. |
| `POSTGRES_USERNAME` | Database user. Default `django`. |
| `POSTGRES_PASSWORD` | Database password. |

> [!note]
> If one of these is missing, the connection is built with the literal string
> `envvar_not_existing` in its place, and the failure surfaces as a connection
> error naming a host or database you have never heard of. That string in a
> stack trace means "an env var was not set", not "DNS is broken".

## Redis

| Variable | Purpose |
|---|---|
| `REDIS_HOST` | Host of the Redis instance. Used for the cache, the Celery result backend, and the cluster cancel index. |
| `REDIS_USERNAME` / `REDIS_PASSWORD` | Credentials for it. |

All three are combined into a `redis://` URL — there is no separate URL
variable for the app itself. (The dashboard proxy has its own
`TOKEN_REDIS_URL` / `REDIS_URL`, listed under Streamlit above, because it can
run as a separate process against a different instance.)

## Deployment identity

| Variable | Purpose |
|---|---|
| `DEPLOYMENT_ENVIRONMENT` | Presence flag: set to anything on a deployed instance, leave unset on a developer machine. When set, `lex start` runs `collectstatic` first, the audit-log cache switches to its shared backend, and the framework is allowed to call the platform API. Unset, all three are skipped. |
| `LEX_ENVIRONMENT_TAG` | Set to `dev` to turn Django's `DEBUG` on. Any other value — including unset — leaves it off. This is the only switch for `DEBUG`; there is no `DJANGO_DEBUG`. |
| `KUBERNETES_ENGINE` | Anything other than `NONE` tells the framework it is running under Kubernetes, which changes how static files are served. Default `NONE`. |
| `K8S_NAMESPACE` | Namespace, used to build storage paths for uploaded files. |
| `INSTANCE_RESOURCE_IDENTIFIER` | The name that distinguishes this instance from every other one sharing infrastructure. It becomes the Celery queue name, the cache-key prefix, the Redis key prefix for recovery and cancellation, and part of the upload path. Two instances that share a Redis or a broker **must** have different values here. Defaults vary by call site (`celery`, `local`) — set it explicitly on anything deployed. |
| `PROJECT_ROOT` | Absolute path to your project. Everything that resolves a project-relative path — `lex_config.py`, `initial_data`, migrations — starts here. Defaults to the current working directory, which is why `lex` commands behave differently depending on where you run them. |
| `PROJECT_DISPLAY_NAME` | Name shown in the frontend's title bar. Defaults to the repository name. |
| `DOMAIN_BASE` | Hostname of the platform API the framework calls for transactional mail and client-role lookups. |
| `PUBLIC_URL` | Fallback public URL for the dashboard when `STREAMLIT_PUBLIC_URL` is unset. |
| `LEX_API_KEY` | API key for those platform API calls, and the key the framework's own health endpoint expects. See [[ship-and-operate/monitoring and health]]. |

## File storage

| Variable | Purpose |
|---|---|
| `STORAGE_TYPE` | Where uploaded files go: `SHAREPOINT`, `GCS`, or `LEGACY`/unset for local disk. |
| `GS_BUCKET_NAME` | Bucket name when `STORAGE_TYPE=GCS`. |
| `SHAREPOINT_URL` | Site URL when `STORAGE_TYPE=SHAREPOINT`. Default `local`. |
| `SHAREPOINT_APP_CLIENT_ID` | Azure app registration client ID for SharePoint access. |
| `SHAREPOINT_API_TENANT_NAME` | Azure tenant name. |
| `SHAREPOINT_API_CERTIFICATE_PATH` | Path to the certificate used to authenticate as that app. |
| `SHAREPOINT_API_CERTIFICATE_THUMBPRINT` | Thumbprint of that certificate. Azure requires both the file and its thumbprint. |
| `FILE_PREVIEW_LINK_BASE` | Base URL used to build the in-browser preview link for a stored document. |

## Celery tuning

Distinct from the recovery knobs above: these bound a task's own execution
rather than what happens when a worker dies.

| Variable | Default | Purpose |
|---|---|---|
| `CELERY_TASK_TIMEOUT`    | `3600` | Hard time limit, in seconds, for a single task. |
| `CELERY_MAX_RETRIES`     | `3`    | Retries for a task that raises. |
| `CELERY_RETRY_DELAY`     | `60`   | Seconds between those retries. |
| `CELERY_VALIDATE_CONFIG` | `True` | Whether the worker checks its broker and backend configuration at startup. Leave it on: the check turns a class of silent misconfiguration into a startup error. |

## Dashboard proxy: tokens and sessions

These govern how the [[access-and-dashboards/streamlit dashboards|dashboard proxy]]
handles the tokens it holds on a user's behalf. The defaults are correct for a
normal deployment.

| Variable | Default | Purpose |
|---|---|---|
| `TOKEN_IDLE_TTL_SECONDS` | `28800` (8 h) | How long a dashboard session survives with no activity before its tokens are dropped. |
| `TOKEN_EXPIRY_SKEW_SECONDS` | `30` | How far before real expiry a token is treated as expired, so a refresh happens before a request fails. |
| `TOKEN_REDIS_PREFIX` | `st_proxy_tokens:` | Key prefix for the token store. Change it when two deployments share one Redis. |
| `ST_ACCESS_COOKIE_MAX_AGE` | `600` | Lifetime of the proxy's access cookie. |
| `JWT_ALG` | `RS256` | Algorithm the proxy requires when validating tokens. |
| `JWT_LEEWAY_SECONDS` | `2` | Clock-skew tolerance for `exp` and `nbf`. |
| `TRUSTED_PROXY_HOSTS` | `*` | Hosts the proxy accepts forwarded headers from. Narrow this if the proxy is reachable from outside your ingress. |
| `UPSTREAM_KEEPALIVE_EXPIRY_SECONDS` | `2` | How long an idle upstream connection to Streamlit is kept open. |
| `LEX_PROXY_SHUTDOWN_TIMEOUT` | `5` | Seconds `lex streamlit` waits for the proxy thread to stop before giving up on a clean shutdown. |
| `STREAMLIT_PUBLIC_URL` | — | Public URL of the dashboard. Falls back to `PUBLIC_URL`. |
| `LEX_STREAMLIT_BASE_URL_PATH` / `STREAMLIT_SERVER_BASE_URL_PATH` | — | Sub-path the dashboard is mounted under, when it is not at the root. The first wins if both are set. |
| `LEX_STREAMLIT_QUACKBACK` | on | The feedback launcher on dashboard pages. Opt **out** with `0`, `false`, `no`, or `off` — a deployment that already shows the widget in the main app normally wants it here too. |
| `SET_ST_ACCESS_COOKIE` | `true` | Whether the proxy sets its short-lived access cookie at all. |
| `PERSIST_JWT_AUTH_TO_SESSION` | `true` | Whether a validated token is written into the session, so later requests skip revalidation. |
| `JWT_VERIFY_ISSUER` | `false` | Whether the `iss` claim is checked against `OIDC_ISSUER`. Off by default because a mismatch between the internal and external Keycloak URL is common and non-fatal; turn it on once you have confirmed the issuer your tokens actually carry. |
| `OIDC_VERIFY_SSL` | `true` | TLS verification on calls to Keycloak. Only turn this off against a local server with a self-signed certificate. |
| `UPSTREAM_USE_SYSTEM_PROXY` | `false` | Whether HTTP calls to Streamlit honour the system proxy variables. Off by default: an `HTTP_PROXY` meant for outbound traffic will otherwise swallow a loopback connection. |
| `WS_UPSTREAM_USE_SYSTEM_PROXY` | `false` | The same switch for WebSocket connections, which is separate because the two use different clients. |
| `UPSTREAM_MAX_CONNECTIONS` | `100` | Connection-pool ceiling for the proxy's calls to Streamlit. |
| `UPSTREAM_MAX_KEEPALIVE` | `20` | How many of those may stay idle in the pool. |
| `LEX_SERVE_STATIC_LOCALLY` | `true` | Whether the proxy serves Streamlit's static assets itself instead of passing them upstream. |
| `LEX_PROXY_ACCESS_LOG` | `true` | Access logging for the proxy. |
| `LEX_PROXY_ACCESS_LOG_STATIC` | `false` | Whether that log includes static-asset requests. Off by default — they dominate the volume and say nothing. |

## Audit logging

| Variable | Default | Purpose |
|---|---|---|
| `INITIAL_DATA_AUDIT_LOGGING` | on | Whether loading initial data writes audit-log entries. Accepts `true`/`1`/`yes`/`on`/`enabled` and their negatives. See [[history-and-audit/audit logs]]. |

> [!warning]
> A value this variable does not recognise — `maybe`, a stray quote, a typo —
> raises at startup rather than falling back to the default. That is deliberate:
> a misspelled switch that silently keeps auditing on is worse than one that
> stops and tells you. The error names the variable and lists the accepted
> values.

## Behaviour switches

Two knobs that change how the framework behaves rather than what it connects to.

| Variable | Default | Purpose |
|---|---|---|
| `LEX_METADATA_CACHE_SECONDS` | `30` | How long a browser may reuse the model-structure response. This is the staleness budget for a permission change: a revoked permission can stay visible in the model tree for at most this long. Set `0` where that is unacceptable — the tree is then re-fetched on every navigation. |
| `LEX_SYNC_STREAMING_EXPANSION` | `true` | Whether synchronous batch expansion streams combinations one at a time (the memory-safe path) or materialises them all first. Set `false` for a one-line rollback to the old behaviour without a redeploy. See [[calculations/batch calculations]]. |

## Used only by the history backfill script

`lex/backfill_history_sql.py` is a standalone script that connects on its own
rather than through Django, so it reads a different set of names. These have no
effect on the application.

| Variable | Default |
|---|---|
| `DB_HOST` | `localhost` |
| `DB_USER` | `postgres` |
| `DB_PASSWORD` | `postgres` |
| `DB_NAME` | a hard-coded name — always set this one explicitly |

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
