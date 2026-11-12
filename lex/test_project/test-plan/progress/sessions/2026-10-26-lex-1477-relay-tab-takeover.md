---
date: 2026-10-26
clusters: [1]
tests_added: "8 new scenarios (1.415-1.422)"
suite_tally: "init +8 (batch 1az, 8 pass / 0 fail, 13 subtests); no other cluster touched"
---

# Batch 1az — a machine endpoint on the dashboard origin cannot take over the user's tab

See [1az](../../clusters/01-init/batches.md). Reported as LEX-1477, against 2.1.11: *"clicking a
model that has a streamlit embedding sends the whole browser tab to the streamlit site of the
instance"*, landing on `https://<instance>-streamlit.../_lex/theme-relay`, intermittently, with no
reliable way to trigger it.

The reported URL is the whole of the diagnosis. Nothing in either codebase navigates anywhere near
that path — but it is exactly where an OIDC round trip leaves a browser when the request that
failed authentication was the hidden theme-relay frame. The frontend has framed
`/_lex/theme-relay` since the cross-origin theme work; the proxy only started *serving* it in
2.2.0 (`8780a119`), and 2.1.11 is below that line. On 2.1.11 the frame therefore fell into the
authenticated catch-all, 401'd, and was answered with the frame-breakout page — whose first
statement is `window.top.location = <login>` and whose `next` is whatever path 401'd. Keycloak had
a live SSO session, so the callback returned at once and honoured it. The intermittency is the
cross-origin top-navigation rule: the frame needs transient user activation to move the top
window, which is exactly what clicking **Period** in the sidebar supplies.

So the cause was already fixed, by a commit written for an unrelated reason, and the customer is
on the one version between the frontend shipping the frame and the proxy learning the route. That
makes "add the route" the wrong place to stop. What produced the outcome is two rules that never
asked what kind of path they were acting on: *anything* that 401s inside a frame may escalate to
moving the top window, and *anything* that 401'd is where the browser is returned afterwards.
Both are now bounded — only a page may escalate, only a page may be landed on — and the relay
itself sends a browser that reaches it anyway back to the configured frontend rather than leaving
it on a blank document.

Letter `ay` and scenarios 1.410–1.414 are reserved by open PR #853 (`ALLOWED_HOSTS` in the
cluster), so this batch takes `az` and continues from 1.415 — the same convention as `1av`
reserved / `1aw` taken. The branch is cut from `lex-app-v2` rather than stacked on #853, so an
urgent customer fix is not gated on unrelated work; both append to the same plan shards at EOF,
so whichever merges second wants a keep-both resolution and a dashboard rebuild.

1.419 is deliberately the counterweight. The breakout exists because Keycloak sets
`frame-ancestors 'self'`, so redirecting an expired dashboard frame to the IdP shows "refused to
connect" (1.211); a narrowing that caught the dashboard too would trade this bug for that one.

The other half is in `process-admin-general-client` (batch F12u). The shell accepted
`lex-auth-required` on *origin* alone, and the relay frame shares the dashboard's origin — so an
invisible frame could ask the shell to navigate the whole tab, and the shell had no way to tell it
from the dashboard the user was looking at. That is now attributed to the dashboard frame's own
`contentWindow`, the relay frame is sandboxed to `allow-scripts allow-same-origin` so nothing
served there can navigate the tab at all, and the login URL a re-auth message names must be on the
dashboard's own origin — it was previously only required to be `http(s)`, so an absolute URL
elsewhere passed.

Not fixed here, and worth its own look: `_THEME_RELAY_ALLOWED` falls back to
`http://localhost:8000` when neither `REACT_APP_URL` nor `LEX_FRONTEND_URL` is set, which in a
deployment means the relay's `frame-ancestors` names a host the frontend is not on and the browser
refuses to frame it — theme sync then does nothing, silently. 1.421 keeps that fallback from also
becoming a redirect target, but the underlying footgun stands.
