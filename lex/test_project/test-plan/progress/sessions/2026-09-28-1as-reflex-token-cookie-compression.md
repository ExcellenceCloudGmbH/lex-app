---
date: 2026-09-28
clusters: [1]
tests_added: 5
suite_tally: "init + permissions + api_layer: 808 pass / 0 fail / 16 skip (all environmental) — 1as 5 pass, 1ar 6 pass"
---

# Batch 1as — a Keycloak access token too large for its cookie is stored compressed

The automatic sign-in of [batch 1ar](../../clusters/01-init/batches.md) still proved unreliable
against a real Keycloak, and the cause was not the sign-in: Reflex Enterprise keeps the session, and
a user's tabs in step, through cookies, and a Keycloak access token with full scope — every role in
every client of a shared realm — is larger than the 4096 bytes a browser keeps. A second tab's
sign-in signed the first out, and a restart or a new tab signed the user out; reproduced in a browser
with a token Chromium itself refused. With Keycloak's configuration out of reach, lex-app now stores
such a token compressed and restores it where the plugin reads the cookie
([batch 1as](../../clusters/01-init/batches.md), 1.376–1.380).

What the work taught, beyond the scenarios themselves:

* **A provider cannot override the plugin's vars.** Reflex Enterprise's providers are mixins, and
  Reflex copies a mixin's computed vars over a subclass's own — a redefined `_access_token_metadata`
  is silently ignored. The plugin's one reader of the cookie, `AccessTokenMetadata.from_cookie_value`,
  is patched instead; 1.380 fails if the plugin ever reads the cookie another way.
* **Emulating a refused cookie misleads.** Stripping `Set-Cookie` in a Playwright route left the
  cookie in the jar — `route.fetch()` shares the context's cookie jar — and the synchronous handler
  stalled every sync by twenty seconds. Only a genuinely oversized token, which Chromium refuses on
  its own, showed the real behaviour.
* **Compressing a random token enlarges it**, so the compressed form is kept only when smaller; and
  deflating base64 of deflated data never shrinks it, which makes the "already compressed" check
  equivalent under mutation while that guard stands.

1.375 (batch 1ar) now warns about a random token in the cookie's `access_token=…` form; its old
`"a" * 5000` never reached compression, being in no form at all.
