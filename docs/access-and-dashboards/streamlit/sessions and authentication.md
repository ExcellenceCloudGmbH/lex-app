---
title: Sessions & Authentication
---

A dashboard runs as the person looking at it. Nothing on the pages before this one asks you to configure that — the identity arrives on its own, and the dashboard can call the API with the viewer's permissions. This page is what is happening underneath, and the settings that matter before you deploy.

## Signing in happens once

A dashboard embedded in the Lex App frontend signs in through the app, so the user does not log in again for Streamlit. `LEX_STREAMLIT_AUTH` decides how; set the same value on the backend and on the Streamlit pod:

| `LEX_STREAMLIT_AUTH` | How the dashboard signs in |
|---|---|
| `legacy` (default) | The frontend hands the user's access token to the proxy on the frame's first request. The proxy turns it into a session cookie, redirects to the same view without the token, and renews the tokens itself. |
| `handoff` | The dashboard signs in through the app's own sign-in and holds no token at all. Recommended. |
| `dual` | Both, for a rollout: a dashboard signed in the old way keeps working, a new one hands off. |

Either way you get four things without writing any code:

- **No second sign-in** — the user does not log in again for Streamlit.
- **Identity you can trace** — actions in the dashboard are linked to the user's [Keycloak](https://www.keycloak.org/documentation) identity.
- **The viewer's permissions** — the dashboard can [call the Lex App API as them](#calling-the-lex-app-api-as-them), and [[access-and-dashboards/permissions|permissions]] apply exactly as they do in the grid.
- **Dashboards that stay open** — tokens are renewed in the background, and disconnected Streamlit sessions are kept long enough to survive re-authentication or a network blip.

Defining the dashboard methods is the whole of the developer's part.

## Signing in through the app

With `handoff`, the dashboard's first load goes through the app, inside the frame, where the user is already signed in. It comes back with a cookie, `__Host-lex_dash`, which names the user's sign-in in the app and nothing else: no token reaches the browser or the address bar. From then on the proxy asks that sign-in for a current access token on every request. So:

- **The dashboard stays signed in as long as the app does** — weeks, not minutes. See [[access-and-dashboards/sign-in and sessions|Sign-in & Sessions]].
- **Signing out ends it.** Signing out of the app, Keycloak ending the session, or an administrator deactivating the user ends the dashboard's sign-in too. The frame then asks the app to sign the user in again: within a minute while the dashboard is visible, at once when it becomes visible again — also when its connection had dropped, without a reload.
- **An outage does not.** When Keycloak cannot be reached, the dashboard keeps the token it has while that is valid, then runs with the user's identity alone until Keycloak answers again. Nobody is signed out.
- **Your code gets only what it needs:** the access token and the user's identity, never a refresh token — see [Who is signed in](#who-is-signed-in).

The cookie slides with use: the dashboard's own pages renew it, at most once a day. In `dual`, a dashboard whose hand-off sign-in has ended keeps working through the old sign-in, and the dead cookie is cleared.

Put the dashboard on a host that shares a site with the app — `app.acme.com` and `dash.acme.com`, say — and list both in `LEX_INSTANCE_HOSTS`; hosted instances get this from the platform. A browser that refuses the dashboard's cookie inside a frame gets a page offering to allow it, or to open the dashboard in a tab of its own, instead of a sign-in loop.

To sign out of the dashboard alone, open `/oauth2/logout` on it; `/oauth2/sign_out` signs the user out of the app as well.

In every mode, `legacy` included, the proxy accepts a dashboard's live connection only from the dashboard's own page: a WebSocket whose `Origin` is not the dashboard's host (the one the browser used, a dashboard host in `LEX_INSTANCE_HOSTS`, or `STREAMLIT_URL`) is closed with `4403`. Clients that are not browsers send no `Origin` and are authenticated as before.

## Staying signed in with `legacy`

Access tokens are short-lived and dashboards often stay open longer than one lasts. With `legacy`, Lex App renews the token in the background through the proxy, without reloading the dashboard, so widgets and `st.session_state` keep their state across a renewal and across normal Streamlit script re-runs.

Sessions still follow Keycloak's maximum lifetime, and a revoked Keycloak session stops working immediately. When renewal genuinely cannot continue, the embedded dashboard asks the surrounding app to re-authenticate and returns the user to the same view. A proxy that does not answer is treated as an outage: the dashboard waits and asks again, and does not sign the user out.

## Who is signed in

Lex App keeps the viewer in `st.session_state`, where any of your code can read it:

| Key | Holds |
|---|---|
| `user_info` | The viewer's claims, a dict: Keycloak's userinfo answer — `sub`, `email`, `preferred_username`, `name` and whatever else the realm returns — or only the first three until Keycloak has answered |
| `user_id` | Keycloak's stable user ID, `sub` — or the email when the sign-in carries no `sub` |
| `user_email` | The email |
| `user_username` | `preferred_username`; without one, the part of the email before `@`, until Keycloak's user info answers with a `preferred_username` or `name` |
| `permissions` | The viewer's Keycloak UMA permissions: a list with one `{"rsname": …, "scopes": […]}` entry per resource, asked for again with each new access token. Empty until Keycloak has answered, and when it grants nothing or cannot be asked |
| `access_token` | The viewer's Keycloak access token, a string, renewed in the background; `""` when there is none |
| `auth_method` | How the proxy signed the dashboard in: `"handoff"`, `"session"` or `"jwt"` |

```python
import streamlit as st


def main():
    me = st.session_state.user_info
    st.caption(f"Signed in as {me.get('name') or me.get('preferred_username')}")

    resources = sorted({p["rsname"] for p in st.session_state.permissions})
    st.write("Keycloak resources you hold a permission on:", resources)
```

A record's `streamlit_main(user)` receives `user_info` as `user`; `streamlit_class_main()` and `main()` receive nothing, so they read the keys above.

## What they may see

The ORM reads as the dashboard, **not** as the viewer. Lex App's [[access-and-dashboards/permissions|permission methods]] — `permission_read` and the rest — guard the API and the grid; `Fund.objects.all()` in a dashboard returns every fund, whoever is looking. Decide what a viewer may see from `permissions` — a model's resource is named `<app_label>.<ModelName>` — or read the data through the API as them.

## Calling the Lex App API as them

Send `access_token` as a bearer token, and the Lex App API answers as the viewer, with their permissions applied exactly as in the grid:

```python
import httpx
import streamlit as st


def main():
    token = st.session_state.access_token
    if not token:
        st.warning("Your sign-in cannot be checked right now. Try again in a moment.")
        return
    response = httpx.get(
        "http://localhost:8000/api/model_entries/fund/list",
        headers={"Authorization": f"Bearer {token}"},
    )
    st.dataframe(response.json())
```

`http://localhost:8000` is `lex start` locally; in production it is wherever the dashboard reaches the Lex App backend. Without `perPage` the list answers a plain array of every row the viewer may read — see [[reference/REST API#The list endpoint|The list endpoint]].

The token never reaches the browser. While Keycloak cannot be reached under `handoff` there may be no usable one: a dashboard opened during the outage has `""`, and one opened before it keeps its last token, which the API refuses once it has expired. Read a refusal then as "try again later", not as signed out.

## Before you deploy

None of this needs attention in development.

### With `handoff`

The Streamlit pod reads the app's database, which `lex streamlit` already connects it to, and needs `LEX_INSTANCE_HOSTS` to find the app. It needs no Redis, however many replicas run: the proxy keeps no tokens. Run `lex prune_dashboard_sessions` once a day with `lex oidc_cleanup`; the platform does this for hosted instances.

The proxy answers its own routes under `/auth/` (`/auth/handoff`, `/auth/required`, `/auth/status`, `/auth/token`). An ingress that sends the whole dashboard host to the proxy needs nothing more; one that routes by path must include `/auth/`. On HTTPS only the `__Host-lex_dash` cookie counts; the unprefixed `lex_dash` exists for development proxies on plain HTTP.

With `legacy` and `dual`, two settings decide whether dashboard sessions survive restarts and load balancing.

### Session cookie signing

The proxy signs session cookies with the first of these it finds:

1. `SESSION_SECRET`, if you set it — an explicit choice always wins.
2. A key **derived from `DJANGO_SECRET_KEY`**, which every deployed instance already has. It is stable across restarts and identical on every replica, which is exactly what session cookies need. It is derived rather than reused, so the cookie key and Django's secret cannot be traded for one another.
3. A random value per process, with a warning in the log.

In practice this means **you usually set nothing**: a deployed instance has a real `DJANGO_SECRET_KEY`, so dashboard sessions survive a redeploy on their own. Set `SESSION_SECRET` when you want to control the value yourself — or when the warning in step 3 appears, which means `DJANGO_SECRET_KEY` is missing or still the shipped default.

### More than one proxy replica

The token store holds the refresh tokens that keep dashboards alive, and the in-memory one is process-local. With two replicas, a session established on one returns `401` on the other — which reaches the user as a session that expired for no reason.

So if you run more than one replica, set a shared `TOKEN_REDIS_URL` (or `REDIS_URL`). The proxy does not let you get this wrong quietly: with `LEX_PROXY_REPLICAS` above 1 and no Redis, it refuses to start and says so.

See [[reference/Environment Variables]] for the full list, including `SESSION_SAMESITE` for cross-site iframe deployments.

## Loading and caching

Streamlit's frontend is a large, code-split bundle. The proxy serves those Streamlit package assets directly — compressed and cacheable — and leaves anything specific to your app authenticated: the dashboard page, WebSocket data, uploads, and `/media/` files.

> [!note] `Failed to fetch dynamically imported module`
> This almost always means the browser is holding a stale cached page that
> points at files from an older Streamlit release. A hard reload resolves it.

## Related

- [[access-and-dashboards/permissions|Permissions]] — what the identity is allowed to see once it arrives
- [[reference/Environment Variables|Environment Variables]] — every setting named on this page
- [[ship-and-operate/deploying|Deploying]] — where these settings go
