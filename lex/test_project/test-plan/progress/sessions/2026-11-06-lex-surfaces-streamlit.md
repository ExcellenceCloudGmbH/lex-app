---
date: 2026-11-06
clusters: [1, 10]
tests_added: "64 new scenarios (1.501-1.540, 1.541-1.556, 10.167-10.174); 1ac 1.238-1.239 updated (the plan deletes Streamlit's own refresh); review fixes: 1.533-1.540 and 1.554-1.556 added, 1.524 and 1.551 rewritten"
suite_tally: "clusters 01+04+09+10: 1093 pass / 18 skip / 11 fail -- 4 also fail on untouched 2.4.2 (1aq 1.357c and 1.368, 1at 1.381 x2 subtests; 2.4.2 on codeflow 1.1.1: 948 pass / 16 skip / those 4), and 1aw x7 fail only from the symlinked checkout (18/18 pass from a real copy). After the last proxy refinement: 1be + 1bf + 10ab + legacy guards (1z, 1aa, 1ac, 1ad, 1an) + 10aa: 159 pass / 0 fail. test_plan_aggregates validate: the same 79 problems as untouched 2.4.2. After the review fixes, from a real copy: 1be + 1bf + 10ab + legacy guards + 10aa 205 pass / 0 fail; whole init + api_layer 889 pass / 16 skip / 4 fail (exactly the 2.4.2 four)"
---

# Batches 1be, 1bf and 10ab — Streamlit and MCP embeds sign in through the app (long-lived sessions, P5)

See [1be](../../clusters/01-init/batches.md), [1bf](../../clusters/01-init/batches.md) and
[10ab](../../clusters/10-api_layer/batches.md).

Plan phase P5, §6.3 and §6.5 (MCP embed), on top of lex-core's 2.5 work (1bd, 4y, 10aa). A Streamlit
dashboard no longer holds Keycloak tokens: it hands off through the app's `oidc/handoff` inside its
frame, keeps an opaque handle in `__Host-lex_dash`, and every request asks the app's sign-in for a fresh
access token (1be: the proxy, the Streamlit process, the cookies, `LEX_STREAMLIT_AUTH`; 10ab: the app's
endpoint and what `api/auth/*` advertise). MCP embeds carry a single-use `lex_embed_ticket` instead of an
access token and become a read-only cookie that ends with the token (1bf).

Found on the way and fixed: BUG-037, the dashboard proxy forwarded client-supplied identity and token
headers to Streamlit ahead of its own (identity spoofing for any signed-in user).

Two older scenarios asserted what the plan removes -- Streamlit spending its own refresh token
(1ac 1.238-1.239) -- and now assert the replacement; see the 1be batch.

**Review fixes (2026-11-06).** An independent security review of 1be/1bf found, and this change fixes:
cross-site WebSocket hijacking in every `LEX_STREAMLIT_AUTH` mode (BUG-039, shipped in v2.4.2: a socket
from another origin is now refused 4403 before any credential is read; 1.533); open redirects from a
path decoding to `//host` -- the legacy `auth_token` strip (BUG-040, shipped), the hand-off's marker,
503 and cookie-blocked targets (1.535) and the MCP embed's ticket strip (1.554); dual mode letting a dead
handle block the legacy session's renewal (1.534); the daily handle-cookie re-set used up by answers that
cannot set it (1.537); the token pull honouring the plantable unprefixed cookie on https (1.538); a frame
whose socket dropped after its sign-in ended spinning forever -- now `/auth/status` and a watcher in the
dashboard document end it on the v2 `required` message (1.539), and the Streamlit recovery, which tried
to navigate the dashboard from its sandboxed component frame, now runs in the dashboard document (1.524
rewritten); a deactivated user's dashboard staying signed in (1.540); the redeem locking the sign-in row
(1.536); the embed ticket in model-visible tool output (now only in `_meta`, 1.551 rewritten), a HEAD
spending a ticket (1.556), and the frame that holds an embed losing it when it renders its ticket again
(1.555). Nineteen mutations, one per fix, each fail their scenario. Accepted without change: concurrent
hand-offs rewriting the nonce cookie, and embed tickets not bound to a browser (see the 1be and 1bf
batches). After the fixes, from a real copy: be 40 / bf 16 / 10ab 8 pass, and the whole init + api_layer
clusters fail only the four tests that fail on untouched 2.4.2.
