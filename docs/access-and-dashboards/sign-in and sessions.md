---
title: "Sign-in & Sessions"
---

A user signs in once, through [Keycloak](https://www.keycloak.org/documentation), and stays signed in for as long as
Keycloak keeps their sign-in alive — weeks, not hours. This page explains what holds a sign-in open, what ends it, what
the browser and API clients see along the way, and the few things to configure and run.

## How long a sign-in lasts

Keycloak decides. The realm's *SSO Session Idle* and *SSO Session Max* lifetimes are the whole policy; the app has no
clock of its own and never ends a sign-in Keycloak still considers valid.

| | App realms on the platform | Central sign-in (`lex_central`) |
|---|---|---|
| Without activity | 30 days | 7 days |
| At most, however active | 90 days | 30 days |
| Access token | 5 minutes | 5 minutes |

Activity in any app of the realm keeps the sign-in alive. The app's own session cookie lives
`LEX_SESSION_COOKIE_AGE_DAYS` (31) days and is renewed while the user is active, so it never runs out first. See
[[reference/Environment Variables#Sign-in sessions|Environment Variables]].

## What ends a sign-in

| Event | What the user sees |
|---|---|
| The user signs out (in this app, or "sign out everywhere") | The sign-in page |
| Keycloak refuses to refresh the tokens: the SSO session expired, was ended by an administrator, or the user was disabled | The next request is answered `401 session_expired`; the frontend offers to sign in again |
| The user signs out of **another** app of the same Keycloak session | Within seconds, through OIDC back-channel logout; the same `401 session_expired` on the next request |

Nothing else ends a sign-in. In particular, none of these do:

- **Keycloak is unreachable**, slow, or answers with a server error. Requests that need a fresh token are answered `503
  auth_unavailable` with a `Retry-After` header; the frontend waits and retries. The user stays signed in.
- **A 403.** "Not allowed" is an answer about one action, not about the sign-in.
- **A restart** or a deploy of the app, or a request landing on another replica. Sign-ins are stored in the database,
  not in a process.
- **Several tabs** refreshing at the same moment. One refresh runs; the others wait for it and use its result.

## Roles and permission changes

The user's roles on the app's Keycloak client come from the **access token** of their sign-in
(`resource_access.<client id>.roles`), else from a `client_roles` claim in the ID or access token. They are what
`UserContext.client_roles` holds
(see [[access-and-dashboards/permissions|Permissions]]). Keycloak re-evaluates them at every token refresh, so granting or revoking a role takes effect within one
access-token lifetime (5 minutes) of the user's next request. The user does not need to sign in again, and the app does
not call Keycloak's userinfo endpoint on page loads.

Keycloak UMA permissions (`keycloak_scopes`) are fetched per access token and cached until that token expires, at most
60 seconds.

## Where the tokens are

On the server only. The browser holds a session cookie (`HttpOnly`, `Secure` when deployed) and never sees a token. The
tokens of each sign-in are stored encrypted in the database. Unless `OIDC_TOKEN_ENCRYPTION_KEYS` is set, the key is
derived from `DJANGO_SECRET_KEY`; a dedicated key lets you rotate one without the other.

## For frontends and API clients

The bundled frontend handles all of this already. A frontend or script of your own sees these answers.

**A background request** (fetch, XHR, any non-navigation):

| Status | Body | Meaning | What to do |
|---|---|---|---|
| `401` | `{"error": "not_authenticated", "login_url": "..."}` | No sign-in | Sign in, then retry |
| `401` | `{"error": "session_expired", "login_url": "..."}` | The sign-in ended | Sign in, then retry |
| `503` | `{"error": "auth_unavailable", "retry_after": 5}` | Keycloak is unreachable | Wait `Retry-After` seconds and retry; do **not** sign the user out |
| `403` | `{"detail": "...", "code": "permission_denied"}` | Not allowed | Tell the user; stay signed in |
| `403` | `{"detail": "...", "code": "csrf_failed"}` | CSRF token missing or stale | Re-read the `csrftoken` cookie and retry once |

A **navigation** (the browser loading a page) is redirected to the sign-in instead.

**Session status.** `GET api/auth/session` answers `200 {"authenticated": true, "user": {...}, "capabilities": {...}}`
for a signed-in browser, else the 401 or 503 above. A `404` means a server older than this behaviour.

**Signing in again without leaving the page.** Load `oidc/authenticate` in a hidden iframe with `prompt=none`,
`next=oidc/auth-complete?m=silent` and `fail=oidc/auth-complete?m=silent&error=1` (each URL-encoded). If Keycloak still
has a session, the user is signed in again without seeing anything. The `auth-complete` page then posts
`{"type": "lex-auth", "v": 2, "event": "result", "mode": "silent", "ok": true}` to its parent, restricted to its own
origin. If Keycloak has no session, `ok` is `false` with `error: "login_required"`. The next step is then a popup
(`m=popup`), or the full sign-in in the tab. Keycloak's sign-in page is never shown inside a frame.

**Scripts and integrations** send `Authorization: Bearer <access token>`. Only access tokens are accepted, never ID
tokens. A script with no Keycloak user of its own sends an [API key](#api-keys) instead.

The exact wire format, with fixtures, is the *HTTP auth contract v2* in the lex-app repository
(`lex/contracts/auth/v2/`). Its TypeScript client is what the bundled frontends use.

## API keys

An API key lets a script or an integration call the API without a Keycloak sign-in. A key is an `APIKey` row of the
`rest_framework_api_key` app in the instance's database, with a name. A request sends it in one of three ways:

| How | Looks like |
|---|---|
| The `Api-Key` header | `Api-Key: <key>` |
| The `Authorization` header | `Authorization: Api-Key <key>` |
| The `api_key` query parameter, for a caller that can only be given a URL | `?api_key=<key>` |

They are tried in that order, and the first one present is the one checked: a wrong key in the `Api-Key` header is
refused even when the URL carries a valid one. Prefer a header. A key in a URL ends up in access logs.

A request with a valid key and no sign-in runs as a technical user named after the key: all six
[[access-and-dashboards/permissions#Access Scopes|scopes]], the client role `api_key` and nothing else — no groups,
never a superuser, no email. The audit log and `created_by` record it as `Technical User`. A request that is also
signed in, with a session cookie or a bearer token, runs as that user and not as the key. What a key may then do is up
to the model's permission methods; [[access-and-dashboards/permissions#Callers with an API key|Permissions]] has how
they see it. Where a key differs from a signed-in user — lists that come back empty, a refusal that answers `401`, the
routes that take no key — is under [[reference/REST API#API keys|REST API]].

## Dashboards and live updates

The dashboards and the live channels follow the same sign-in.

- **Streamlit dashboards** sign in through the app with `LEX_STREAMLIT_AUTH=handoff`: they hold no token of their own
  and stay signed in exactly as long as the app. The default, `legacy`, keeps the pre-2.5 behaviour. See
  [[access-and-dashboards/streamlit/sessions and authentication|Streamlit: Sessions & Authentication]].
- **Reflex dashboards** keep their own Keycloak sign-in, which now ends only when Keycloak ends the session; an outage
  or several open tabs no longer sign anyone out. See
  [[access-and-dashboards/reflex/sessions and authentication|Reflex: Sessions & Authentication]].
- **WebSockets** (live calculation status, logs, record updates) can be limited to signed-in users with
  `LEX_WS_AUTH=enforce`. A socket without a sign-in is then accepted and closed with `4401`; `4403` means not allowed and
  `4503` that the sign-in cannot be checked right now, so try again. The default, `log`, serves every socket and logs
  the ones `enforce` would close. See [[ship-and-operate/deploying#Live updates and sign-in|Deploying]].
- **Pages embedded by an MCP client** carry a single-use ticket, valid for 60 seconds, instead of a token. The app
  trades it for a read-only cookie that lasts no longer than the MCP client's token. Only the widget gets the address
  with the ticket: it is in the tool result's `_meta.embed_url`, which MCP hosts never show the model, so a custom widget
  must read it from there. Only a `GET` spends a ticket, so link previews and prefetches do not. Rendering the widget again
  in the same frame keeps the embed; a used ticket presented anywhere else ends it. A ticket is not tied to a browser:
  whoever opens the address within its 60 seconds gets the read-only embed.

## Operating it

Run these with the app's environment, as any other `lex` command:

| Command | When |
|---|---|
| `lex oidc_check` | After changing anything about Keycloak, the realm or `KEYCLOAK_INTERNAL_URL`: checks discovery, that the issuer is the public one, the signing keys and the token endpoint. Exits non-zero on a problem. |
| `lex oidc_cleanup` | Daily, with `lex clearsessions`: ends sign-ins whose refresh token expired or whose session is gone, and deletes ended ones after 7 days. |
| `lex oidc_rotate_keys` | After putting a new key first in `OIDC_TOKEN_ENCRYPTION_KEYS`, before removing the old one: re-encrypts every stored token. |
| `lex prune_dashboard_sessions` | Daily, with the two above: deletes expired hand-off codes and the dashboard sign-ins that can no longer be used — those of Streamlit dashboards signed in through the app, and of pages embedded by an MCP client (see [Dashboards and live updates](#dashboards-and-live-updates)). |

On the platform, a daily maintenance job runs `lex oidc_cleanup`, `lex clearsessions` and `lex prune_dashboard_sessions`
for every instance.

> [!note] Keycloak client settings
> For back-channel logout to reach the app, the Keycloak client needs a *Backchannel logout URL* and *Backchannel logout
> session required* on. On the platform the Instance Controller sets both: one Keycloak client serves all instances of a
> customer, so the URL points at the Instance Controller, which forwards each logout to every instance. For a client you
> manage yourself, `lex oidc_urls <host> --secure` prints the app's own URLs to enter.

## See also

- [[access-and-dashboards/permissions|Permissions]] — what a signed-in user may do.
- [[reference/Environment Variables#Keycloak / OIDC|Environment Variables]] — `KEYCLOAK_URL`, `KEYCLOAK_INTERNAL_URL` and
  the rest of the sign-in configuration.
