---
title: "Environment Variables"
---

Lex App reads its runtime configuration from environment variables — usually loaded from the `.env` file `lex setup` generates at your project root. This page lists the variables the framework actively reads.

> [!note]
> This index covers the variables the framework reads directly. Your project's Django settings may layer additional ones on top. If a variable isn't listed here, check `lex_app/settings.py` in the installed package.

## Async / Celery

| Variable               | Purpose                                                                                       |
| ---------------------- | --------------------------------------------------------------------------------------------- |
| `CELERY_ACTIVE`        | `true` to dispatch `@lex_shared_task`-decorated functions to Celery workers; otherwise tasks run synchronously in the current process. See [[features/processing/celery and async calculations]]. |
| `IS_RUNNING_IN_CELERY` | Set to `true` inside Celery worker processes so the framework knows it's executing a queued task rather than a web request. Set automatically when you launch via `lex celery` / `lex celery-workers`. |

## Streamlit

| Variable                | Purpose                                                                                  |
| ----------------------- | ---------------------------------------------------------------------------------------- |
| `IS_STREAMLIT_ENABLED`  | `true` to enable the Streamlit toolbar icon in the frontend. See [[features/access-and-ui/streamlit dashboards]]. |
| `LEX_INTERNAL_AUTH_SECRET` | Shared secret the dashboard presents when it asks the auth proxy for a fresh access token. `lex streamlit` mints one automatically, because it runs both halves in a single process; set it explicitly only if you run the proxy and Streamlit as separate processes. |
| `LEX_PROXY_PORT`        | Port the auth proxy listens on. Default `8501`. |
| `LEX_PROXY_INTERNAL_URL` | Full base URL the dashboard uses to reach the proxy, when it is not `http://127.0.0.1:$LEX_PROXY_PORT`. |
| `SESSION_SECRET` | Signs the auth proxy's session cookies. **Set this, and use the same value on every replica.** Without it the proxy signs with a random per-process value, so every restart — and every request that lands on a different replica — silently logs all users out and resets their dashboard state. Refused at startup when `STREAMLIT_URL`/`BASE_URL` is `https`. Also read as `SESSION_KEY` / `SESSION_SECRET_KEY`. |
| `LEX_ALLOW_EPHEMERAL_SESSION_SECRET` | `true` to run without `SESSION_SECRET` on an https deployment anyway. Single-process development only. |
| `TOKEN_REDIS_URL` | Redis holding the proxy's token store, which is what keeps dashboards renewable. **Required beyond one replica** — in memory it is process-local, so a request routed elsewhere finds no session and returns 401. Falls back to `REDIS_URL`. |
| `LEX_PROXY_REPLICAS` | How many proxy replicas are running. Anything above `1` requires `TOKEN_REDIS_URL`/`REDIS_URL` and proxy session affinity, and is refused without them. Default `1`. |
| `SESSION_SAMESITE` | `SameSite` for the proxy's cookies: `lax`, `strict` or `none`. Defaults to `none` on https and `lax` otherwise. A cross-site iframe only receives `none` cookies (browsers compare against the *top-level* site), so `lax` works only while the frontend and the dashboard share one registrable domain. `none` requires `Secure`, and the combination without it is refused at startup because browsers discard such cookies outright. |
| `SESSION_HTTPS_ONLY` | `true` to mark the proxy's cookies `Secure`. Defaults to whether `STREAMLIT_URL`/`BASE_URL` is https; set it explicitly behind a TLS-terminating ingress. |
| `LEX_STREAMLIT_DISCONNECTED_SESSION_TTL` | Seconds Streamlit keeps a disconnected session's state so a reconnecting browser resumes it rather than starting over. Default `600`. Streamlit's own default is 120s, which is shorter than a login round trip. |
| `REACT_APP_URL` / `LEX_FRONTEND_URL` | **Optional.** Origin allowed to hand the auth proxy a renewed dashboard token (`POST /auth/adopt`). Normally unnecessary — the proxy derives it from `DOMAIN_HOSTED`, which every deployed instance already sets. Set one of these only when the frontend is *not* on the instance hostname. A bare host or a full URL both work. Also used by `lex_view()` for the reverse direction. |
| `STRIP_AUTH_TOKEN_FROM_URL` | `true` (default) to redirect the embedded dashboard's first request to the same URL without its `auth_token`, keeping the token out of the address bar, history and `Referer`. |
| `STATIC_ASSET_MAX_AGE` | `max-age` for Streamlit's content-addressed assets, which the proxy serves itself. Default one year. |
| `STATIC_GZIP_MIN_SIZE` / `STATIC_GZIP_LEVEL` | Compression floor and zlib level for served assets. Defaults `500` and `6`. |
| `JWKS_CACHE_TTL` / `JWKS_RETRY_BACKOFF_SECONDS` | How long Keycloak's signing keys are cached (default `3600`), and how long to wait before retrying a failed refresh while continuing to serve the cached keys (default `30`). |
| `UPSTREAM_TIMEOUT_SECONDS` | Timeout for the proxy's requests to Streamlit. Default `30`. |

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

