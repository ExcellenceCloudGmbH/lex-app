---
date: 2026-11-03
clusters: [1, 10]
tests_added: "10 new scenarios (10.72-10.74, 1.423-1.429)"
suite_tally: "see the run recorded in the batch blocks; the reflex-dependent files are not collected locally (reflex not installed)"
---

# Batches 10o and 1ba — the Streamlit sidebar entry stays inside the instance (2.1.x)

Backport of the mainline fix onto this release line. See
[10o](../../clusters/10-api_layer/batches.md) and [1ba](../../clusters/01-init/batches.md).

Reported against 2.1.12, after the 1477 backport shipped: clicking **Streamlit** left the instance
frontend and parked the whole browser tab on `https://<instance>-streamlit.<domain>/?embed=true`.
The `Streamlit` report framed its dashboard host with no credential; a cross-origin frame cannot
authenticate itself, so the proxy 401'd it, and the framed-401 page moved the top window to that
host's login, which a live SSO session completed at once. 10o fixes the fault — the frame now
carries the session's access token, the handshake the React shell's embed already used. 1ba fixes
the amplifier — the framed-401 page only scripts a navigation for a frame this origin itself put
on screen, which is 1az's rule stated about the embedder instead of path by path.

1.428 and 1.429 join the two: the URL the real report builds, loaded as a framed document
against the real proxy app. With the token the proxy banks it, redirects the frame to the
same place without it and serves the dashboard from upstream — the ticket's expected
behaviour. Without it the frame still 401s, but that 401 moves no window. Both fail against
the pre-fix source.

Letters `ba` and `o` rather than mainline's `ay` and `r`: this line spent `az` on the 1477
backport, and the mainline cluster-10 letters have run further. The `Overview` 404-for-unknown-report
behaviour is mainline-only (v2 security hardening) and is deliberately not carried over.
