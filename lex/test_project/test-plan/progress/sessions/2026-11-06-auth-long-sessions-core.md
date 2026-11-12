---
date: 2026-11-06
clusters: [1, 4, 10]
tests_added: "37 new scenarios (1.490-1.500, 4.148-4.158, 10.152-10.166); 1bc 1.484-1.488, 1aa 1.217-1.219, 1av 1.392, 4g 4.31-4.34, 4x 4.138/4.141/4.142/4.144-4.147, 4o 4.98 and 10o 10.90 moved to django-oauth2-codeflow 3.0"
suite_tally: "clusters 01+04+09+10: 1009 pass / 16 skip / 4 fail (2 tests + 2 subtests in 1aq/1at, the same as lex-app-v2 2.4.2 on codeflow 1.1.1); other clusters: 892 pass / 11 skip / 6 xfail / 29 fail, every one also failing on 2.4.2 in the same order (24 calculation_logging, gate_selftest, 11.14) or a stress timing budget that passes when run alone (11.2, 11.13, 11.22)"
---

# Batches 1bd, 4y and 10aa — lex-app 2.5 adopts django-oauth2-codeflow 3.0 (long-lived sessions, P4)

See [1bd](../../clusters/01-init/batches.md), [4y](../../clusters/04-permissions/batches.md) and
[10aa](../../clusters/10-api_layer/batches.md).

The server side of plan phase P4 on the lex-app-v2 line, on top of the P0/P1 hotfixes (1bc, 4x) and
the ALLOWED_HOSTS fix (1ay). Sign-ins move into the library's encrypted `OIDCSession` rows; lex's
settings, routes and `lex init` follow the 3.0 contract (1bd); the permission lookup becomes lazy,
cached per token and never turns a Keycloak outage into a denial (4y); and lex's REST surface answers
the HTTP auth contract v2 that PAC, the IC frontend and atrium now implement, checked against the
contract's shared fixtures (10aa). BUG-008's unauthenticated half is resolved.

The E2E base classes now sign their client in through `oauth2_authcodeflow.testing.create_oidc_session`
and mock Keycloak's UMA call as an external boundary (`keycloak_uma` in the default patch list).
