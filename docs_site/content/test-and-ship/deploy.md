---
title: "Deploy"
description: "How the dashboards run in production, next to lex-app, and the settings they read."
---

The dashboards are a process of their own beside lex-app, built from the same image and reading
the same settings. In production that process runs one command:

```bash
lex reflex run --env prod
```

Reflex's production mode builds the pages and then serves them and the backend on **one port**:
`8502`, unless you choose another with `--frontend-port`, `REFLEX_FRONTEND_PORT` or
`frontend_port` in `lex_config()`. The build happens when the process starts, so a start takes a while;
Reflex installs its frontend tools for it, which is why lex-app's image includes `unzip`.

How the process is started - a container, a pod, a service - is your platform's job. This page is
what lex-app expects of it.

## What the process needs

- **The project and lex-app's settings.** Reflex imports lex-app's own app module, which sets
  Django up, loads the project's models and `_reflex_structure.py`, and builds the app from them.
  It needs the `rxconfig.py` that `lex reflex` writes at the project root (`config = lex_config()`).
- **The same database and Keycloak settings as lex-app.** The sign-in is redeemed in this process,
  against lex-app's database, and the user's access token is refreshed from there too (see
  [How sign-in works](/who-can-do-what/sign-in)).
- **lex-app's API.** `self.api` calls it at `LEX_API_URL` when set - inside a cluster, the app's
  internal address - else at the app's public domain (`DOMAIN_HOSTED`), else at
  `http://localhost:8000`.

## Addresses

The sign-in only works between addresses the instance lists. They come from `LEX_INSTANCE_HOSTS`, a
JSON object with an entry for the app and one for the dashboards:

```json
{
  "app": {"canonical": "app.example.com"},
  "reflex": {"canonical": "dashboards.example.com", "extra": ["dashboards.example.org"]}
}
```

Without it, the app's host comes from `DOMAIN_HOSTED` and the dashboards' from `REFLEX_URL`.

- A dashboard address that is not listed gets "This address is not one of this instance's
  dashboards." instead of a sign-in, and lex-app refuses to send a sign-in to it.
- A deployed instance - one with `DEPLOYMENT_ENVIRONMENT` set - that names no app host answers
  "This instance names no app host to sign in through."
- Put the dashboards on the same site as lex-app: `dashboards.example.com` beside
  `app.example.com`. lex-app's sign-in cookie is only sent to a dashboard's frame from the same
  site, and the sign-in picks the app host that shares a site with the dashboard's host.

The backend's event connection accepts browsers from the `https://` origin of every dashboard
host, canonical and extra. Leave Reflex's `api_url` unset, as `lex_config()` does: the pages then
connect to whichever host the browser reached them on, so one build serves every host.

## Behind a proxy

- **HTTPS.** Over https the sign-in cookies are `__Host-` cookies: `Secure`, `SameSite=None` and
  `Partitioned`, which lets them work inside lex-app's frame.
- **Forward the public scheme and host.** The backend reads `X-Forwarded-Proto` and
  `X-Forwarded-Host` to know which address the browser used, and checks that against the list
  above.
- **Let WebSockets through.** Every event travels over the connection at `/_event`.

## Inside lex-app

Set `IS_REFLEX_ENABLED=true` on lex-app and its sidebar gets a **Reflex** entry that frames the
dashboards, at the dashboard host that shares a site with the one the user is on, else at
`REFLEX_URL`. The dashboards allow only lex-app to frame them: they answer with a
`frame-ancestors` policy naming their own origin and lex-app's `https://` origins (with no app
host configured, any `http://localhost` port). More in [Inside lex-app](/make-it-your-app/inside-lex-app).

## Settings the dashboards read

| Setting | What it does |
| --- | --- |
| `LEX_INSTANCE_HOSTS`, else `DOMAIN_HOSTED` and `REFLEX_URL` | the app's and the dashboards' addresses |
| `DEPLOYMENT_ENVIRONMENT` | set on a deployed instance: `localhost` addresses are no longer accepted |
| `LEX_API_URL` | where `self.api` reaches lex-app's API |
| `LEX_UI_ACTION_THREADS` | threads for `@ui.action`, `@ui.load` and `@ui.rows` bodies; 16 by default |
| `LEX_UI_JOB_THREADS` | threads for `@ui.job` bodies; 8 by default |
| `LEX_UI_PRIVATE_MB_PER_USER` | megabytes of [`ui.private`](/long-running-work/private-results) values one user may hold in each process; 100 by default |
| `LEX_UI_PRIVATE_VALUES` | how many `ui.private` values each process keeps at most; 5000 by default |
| `LEX_UI_LOG_ERROR_TEXT` | `false` keeps an unexpected error's text out of the log, leaving its type; on by default |
| `REFLEX_USE_SINGLE_PORT` | a development run behind one port (below); production needs no setting for it |

Any Reflex option can be set as `REFLEX_<OPTION>` too, as in any Reflex app. The settings for the
dashboards' look are on [Your app's look](/make-it-your-app/look).

## A development run behind one port

Production mode serves everything on one port by itself. A development run has two servers, the
pages' dev server and the backend; where only one port is open, set `REFLEX_USE_SINGLE_PORT=true`:
the backend then answers its own routes and passes every other request - pages and the dev
server's hot-reload connection - to the pages' server. Open the backend's port.
