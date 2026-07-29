---
date: 2026-07-29
clusters: [1]
tests_added: 6
suite_tally: "1z: 6 pass / 0 fail"
---

# Batch 1z — embedded Streamlit token renewal

Follow-up to [batch 1y](../../clusters/01-init/batches.md), which made the embedded
session's expiry a *graceful* re-login instead of a "refused to connect" wall. This
batch removes the re-login.

Renewal turned out to be impossible for two independent reasons, both fixed here:

- the token endpoint never published an expiry, so nothing could schedule against
  it. The only code that returned `expires_in` / `refresh_interval` was unreachable,
  and it self-signed HS256 — which the proxy, validating RS256 against Keycloak's
  JWKS, would have rejected anyway;
- the proxy discarded a renewed token whenever the stored one was still valid. Since
  renewal can only arrive *before* expiry, that discarded every renewal and let the
  session die at the original deadline. Scenario 1.204 is the gate: it fails against
  the pre-fix proxy and passes with it.

The PR's original follow-up asked for a refresh token on the embedded path. That was
deliberately not built — it would have to travel through the browser into the iframe
URL, where it lands in access logs, browser history and `Referer` headers, which is a
worse posture than the short-lived access token it replaces. The frontend renews
instead, proactively against the published expiry and reactively on 1y's
`lex-auth-required` message.
