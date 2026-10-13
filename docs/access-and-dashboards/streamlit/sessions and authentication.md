---
title: Sessions & Authentication
---

A dashboard runs as the person looking at it. Nothing on the pages before this one asks you to configure that — the identity arrives on its own, and the dashboard can call the API with the viewer's permissions. This page is what is happening underneath, and the two settings that matter before you deploy.

## Signing in happens once

When a dashboard is embedded in the Lex App frontend, the user's access token is handed to the Streamlit proxy on the iframe's first request. The proxy immediately turns it into a session cookie and redirects to the same view without the token, so it never sits in the address bar, browser history, or `Referer` headers.

That gives you four things without writing any code:

- **No second sign-in** — the user does not log in again for Streamlit.
- **Identity you can trace** — actions in the dashboard are linked to the user's [Keycloak](https://www.keycloak.org/documentation) identity.
- **The viewer's permissions** — the dashboard can call the Lex App API as them, and [[access-and-dashboards/permissions|permissions]] apply exactly as they do in the grid.
- **Dashboards that stay open** — the proxy refreshes tokens and keeps disconnected Streamlit sessions around long enough to survive re-authentication or a network blip.

Defining the dashboard methods is the whole of the developer's part.

## Staying signed in

Access tokens are short-lived and dashboards often stay open longer than one lasts. Lex App renews the token in the background through the proxy, without reloading the dashboard, so widgets and `st.session_state` keep their state across a renewal and across normal Streamlit script re-runs.

Sessions still follow Keycloak's maximum lifetime, and a revoked Keycloak session stops working immediately. When renewal genuinely cannot continue, the embedded dashboard asks the surrounding app to re-authenticate and returns the user to the same view.

## Before you deploy

Two settings decide whether dashboard sessions survive restarts and load balancing. Neither needs attention in development.

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
