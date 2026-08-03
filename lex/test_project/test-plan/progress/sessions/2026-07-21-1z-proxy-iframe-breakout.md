---
date: 2026-07-21
clusters: [1z]
tests_added: "6 (1.211–1.216) + source in 1 file (lex/proxy.py)"
suite_tally: "1z 6 pass / 0 fail (python -m lex pytest)"
---

**Batch 1z landed — the Streamlit auth proxy now breaks out of the iframe on
re-auth instead of framing Keycloak** (customer report: "auth.excellence-cloud.de
refused to connect" inside the embedded Streamlit dashboard after some time, on
lex-app `2.0.0rc199`). Root cause: `lex/proxy.py` silently logs the embed in from
the `?auth_token=` JWT but stores it with `refresh_token: None`, so the session
can't renew and dies at the realm's 4h `sso_session_max_lifespan`; the deny branch
then did `RedirectResponse("/auth/login")` for any `Accept: text/html` request —
including the iframe's own document load — which loads Keycloak's login page inside
the frame, where `frame-ancestors 'self'` blocks it. Fix: detect a framed document
load via `Sec-Fetch-Dest` and return a 401 that breaks out to a top-level login
(auto top-nav + `postMessage` to the parent shell + a `target="_top"` link);
top-level redirects and API 401s are unchanged. Follow-up (separate change): give
the embedded `auth_token` path a refresh token so the proxy renews silently rather
than only degrading the failure gracefully. See
[batch 1z](../../clusters/01-init/batches.md).
