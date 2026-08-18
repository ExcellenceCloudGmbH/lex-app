---
date: 2026-08-18
clusters: [1]
tests_added: 17
suite_tally: "1ab: 17 pass / 0 fail; init cluster: 315 pass / 13 skip"
---

# Batch 1ab — Streamlit session survival across an idle period

Third and last batch on this auth path. [Batch 1z](../../clusters/01-init/batches.md)
made expiry graceful, [1aa](../../clusters/01-init/batches.md) let the embedded caller
renew, and this one stops the session expiring at all — including on the standalone
(non-embedded) path, which had no renewal mechanism whatsoever.

Reported as: a dashboard left alone for a while shows "❌ Authentication Error: Missing
user information. Please access this application through the main portal." The message
is doubly wrong — the identity headers were present, and the page could not recover
from it.

## The lifetime mismatch

`st.context.headers` is not "the current request". It resolves the *session's client*,
so it is the HTTP handshake that opened the WebSocket — frozen for as long as that
socket lives, which with Streamlit's pings is hours. The access token it carries lives
minutes. Nothing a running script can do produces a newer one.

Session auth then had local refresh **deliberately** disabled, on the reasoning that
renewal is the proxy's job. That is true of the proxy's own traffic and false of the
dashboard's: the proxy can only hand a credential over at handshake time, and the
handshake happens once. So "the proxy handles it" meant nobody handled it.

Three defects compounded it:

- `auth_callback` sets the short-lived `st_access` cookie on every login, and both the
  HTTP and WebSocket paths consulted that cookie *before* the server-side session — so
  a perfectly normal login reached Streamlit as `auth_method="jwt"` with
  `refresh_token: None`, an unrenewable credential, minutes from death;
- `_sync_tokens_from_headers` adopted the header token whenever it merely *differed*
  from the stored one. After a successful renewal the frozen header copy is the older
  one, so every rerun reverted the renewal that had just landed;
- the expiry path called `_invalidate_local_auth`, which cleared user id, email and
  username. Hence the message. And it was sticky rather than transient: the next rerun
  re-read the same frozen headers, found the same expired token, and invalidated again.

## The fix

A pull channel, because a push channel cannot exist. `GET /auth/token` on the proxy
returns a currently-valid access token for the caller's session cookie; the co-located
Streamlit process asks whenever it needs one. Refreshing stays exclusively the proxy's
job, so the refresh token keeps exactly one writer and there is no rotation race — the
hazard that made local refresh unusable in the first place. It is guarded by a shared
secret rather than the session cookie alone: the cookie is HttpOnly, but page script
can still `fetch` with `credentials: 'include'` and read the response body.

Alongside it: credential precedence reordered to explicit `auth_token` → session →
`st_access` cookie on both paths, so the renewable credential is what the handshake
carries; the header token adopted only when strictly newer; renewal scheduled ahead of
expiry for session auth too (which also keeps Keycloak's SSO idle clock from running
out on an idle tab); the refresher bounded by the Streamlit session's actual lifetime
rather than a flag nothing sets when a tab closes; permissions preserved when a UMA
lookup fails instead of blanked; and identity decoupled from token freshness, with
"Missing user information" reserved for the one case it describes — a connection that
never carried the proxy's headers.

When renewal genuinely cannot succeed (SSO max lifetime, a revoked session) the page
rides it out for a grace window and then asks the parent shell to renew via the
`lex-auth-required` message 1z already established, falling back to a `target="_top"`
sign-in link. A Streamlit component iframe is sandboxed without `allow-top-navigation`,
so a programmatic redirect would be silently blocked — the link is a user gesture and
always permitted.

`lex/streamlit_app.py`'s bootstrap moved under its `__name__ == "__main__"` guard so
the module can be imported without firing Streamlit commands; Streamlit executes the
file as `__main__`, so the app is unaffected and the lifecycle became testable.

14 of the 17 scenarios fail against the pre-fix tree. The two that pass (1.228, 1.230)
are pre-existing correct behaviours kept as guards.
