---
date: 2026-11-06
clusters: [1, 4]
tests_added: "19 new scenarios (1.481-1.489, 4.138-4.147); 4.32/4.32b moved to the manager's per-call userinfo"
suite_tally: "clusters 1+4+10: 600 pass / 16 skip / 0 fail"
---

# Batches 1bc and 4x — auth hotfixes for long-lived, secure sessions (P0 + P1)

See [1bc](../../clusters/01-init/batches.md) and [4x](../../clusters/04-permissions/batches.md).

The P0 and P1 steps of the long-lived-sessions plan on the release/2.1.x line. Two of the
fixes are security bugs found while designing longer sessions: per-user Keycloak calls shared one
connection's headers across request threads (BUG-038), and the bearer path accepted HS256 tokens
signed with the client secret (BUG-035). The third, unanchored login exemptions (BUG-036), had let
`/api/user_permissions/` and any path containing `/health` through without a signed-in user.

1bc covers the settings a deployed pod now insists on: its own `DJANGO_SECRET_KEY`, a session that
outlives Keycloak's 30-day idle window, and PKCE on both relying parties of the shared client.
The library pin moves to django-oauth2-codeflow 2.1.0 (the bridge release); all runs here used it.
