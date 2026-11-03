---
date: 2026-11-03
clusters: [1, 10]
tests_added: "8 new scenarios (10.100-10.102, 1.410-1.414)"
suite_tally: "init + api_layer + permissions: 861 pass / 16 skip, 15 pre-existing environment failures (reflex not installed, streamlit version floor, hashed asset bundle) identical on origin/lex-app-v2"
---

# Batches 10r and 1ay — the Streamlit sidebar entry stays inside the instance

See [10r](../../clusters/10-api_layer/batches.md) and [1ay](../../clusters/01-init/batches.md).

Reported after 2.1.12: clicking **Streamlit** left the instance frontend and parked the whole
browser tab on `https://<instance>-streamlit.<domain>/?embed=true`. The `Streamlit` report framed
its dashboard host with no credential; a cross-origin frame cannot authenticate itself, so the
proxy 401'd it, and the framed-401 page moved the top window to that host's login, which a live
SSO session completed at once. 10r fixes the fault — the frame now carries the session's access
token, the handshake the React shell's embed already used. 1ay fixes the amplifier — the
framed-401 page only scripts a navigation for a frame this origin itself put on screen.

The same family as LEX-1477, which named the utility paths a frame may not return a tab to; this
names the embedder instead, so it covers the dashboard's own landing page too. Cluster 1z is
unchanged and still passes: the frame is never redirected to the IdP in any branch.
