---
date: 2026-11-06
clusters: [1, 9]
tests_added: "34 new scenarios: 1.561-1.579 (1bg, Reflex sign-in hardening) and 9.53-9.67 (9j, WebSocket sign-in); plus 6 script tests in .github/scripts/tests/test_check_doc_commands.py (outside the cluster system). Review fixes the same day: 18 more (1.580-1.591, 9.68-9.73), 1.566 revised"
suite_tally: "after the review fixes: 1bg 31 pass with LEX_REFLEX_REDIS_TEST_URL (28 pass / 3 skip without); 9j 21 pass; clusters 01+09 from a real copy of the worktree 829 pass / 14 skip / 4 fail = the 4 that fail on 2.4.2 (1aq 1.357c, 1.368; 1at 1.381 x2); .github/scripts/tests 627 pass. Before the review: 1bg 19 pass with LEX_REFLEX_REDIS_TEST_URL (17 pass / 2 skip without); 9j 15 pass; clusters 01+04+09+10: 1086 pass / 16 skip / 12 fail, i.e. the 4 that also fail on lex-app-v2 2.4.2 (1aq 1.357c, 1.368; 1at 1.381 x2 subtests), 7x 1aw (the checkout name auth-long-sessions as app label, environment) and 1bf 1.542 (lex-surfaces-streamlit, edited during the run; passes alone and after 1bg/9j); crud_api+calculations+celery_async 433 pass / 7 skip on both trees; .github/scripts/tests 627 pass (619 on 2.4.2)"
---

# Batches 1bg and 9j — Reflex dashboards and WebSockets follow the sign-in (long-lived sessions, P5)

See [1bg](../../clusters/01-init/batches.md) and [9j](../../clusters/09-signals_ws/batches.md).

Plan "long-lived sessions", sections 6.4 and 6.5, on top of the P4 core (1bd, 4y, 10aa). A Reflex
dashboard keeps its own Keycloak tokens, and its provider now keeps them as long as Keycloak does
(1bg): the at_hash comes from the access token held now, only `invalid_grant` signs out, one refresh
per refresh token across tabs and replicas with the others adopting the encrypted result, a 30 s
cooldown, 30-day cookies, and back-channel logouts reach the dashboards. The five authenticated
WebSockets serve only a live sign-in and close with the contract's codes behind the rollout flag
`LEX_WS_AUTH` (default `log`) (9j). BUG-047 (anonymous sockets) is resolved under `enforce`; BUG-048
(the two-tab at_hash loop, and the plugin accepting mismatched pairs) and BUG-049 (sign-out on any
refresh or userinfo failure) are resolved.

The docs command gate also accepts the commands the CLI forwards from installed libraries
(`oidc_*`, `clearsessions`) and no longer reports `_`-prefixed helper modules as undocumented
commands (the latter identical to open PR #864).

Cluster 9 skips letters h and i: the in-flight branch `integration/lex-3-extensions` holds them.

**Review fixes (2026-11-06).** An independent security review of 1bg and 9j found 13 issues; 12 are fixed with
scenarios that fail with the fix undone (each mutation-checked in a scratch copy of the source), one is accepted. A
Reflex sign-in whose access token expired while Keycloak cannot renew it now serves no identity, permissions or API
token and `/login` says sign-in is temporarily unavailable, without signing out (1.566 revised, 1.580-1.581); a
userinfo outage never serves another user's cached profile (1.582); Keycloak's answer is verified before it is
stored, with 30 s of clock skew and one key-set refetch for an unknown key (1.583-1.584 -- shipped plugin defects,
BUG-050 and BUG-051); a refresh that keeps the tokens re-arms the timer (1.585); the refresh lock lasts while its
leader works, in memory and in Redis (1.586, 1.591); a published set is adopted only for its refresh token, user and
Keycloak session (1.587); process memory is swept and capped (1.588); a clock stepped back suppresses nothing (1.589);
a back-channel sign-out is audited once (1.590). Sockets: a dropped database connection is 4503 under `enforce` and
never breaks a socket under `log` (9.68); a sign-in that ends while the socket joins its group is closed at once
(9.69); re-validation also closes on a deleted Django session, a deactivated user and an expired Keycloak session,
and reaches synchronous consumers through the channel layer (9.70-9.73). Accepted: the `at_hash` check runs only in a
tab that stored tokens itself -- BUG-048 now says what is guaranteed. See [1bg](../../clusters/01-init/batches.md) and
[9j](../../clusters/09-signals_ws/batches.md).
