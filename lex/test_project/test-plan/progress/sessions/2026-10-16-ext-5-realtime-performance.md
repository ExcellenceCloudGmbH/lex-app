---
date: 2026-10-16
clusters: [9, 10]
tests_added: "9 new scenarios (9.44-9.48, 10.113-10.116)"
suite_tally: "9h 5 pass, 10t 4 pass. Full cluster suite on the complete stack (PRs 1-5; not stress, Reflex modules excluded: no reflex package locally): 1746 pass / 35 fail / 24 skip / 5 xfail - 33 failures are identical to lex-app-v2 before the stack (25 calculation-log rows, 7 Streamlit/asset environment checks, the gate self-test); the other 2 (7.115 stubbing on_commit with one argument, 4.32b patching KeycloakManager) were fixed in PR 3 and PR 5 and pass (48 pass on the affected modules)"
---

# Tenant-scoped realtime events, fewer Keycloak calls; full suite on the complete stack

Fifth of five stacked PRs (feat/ext-5-realtime-performance). broadcast_mutations=False silences a model; MUTATION_GROUPS/USER_GROUPS scope record_mutation and calculation-status events per tenant (payloads unchanged). KeycloakPermissionsMiddleware keeps one KeycloakManager per process; LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS (off by default) caches permissions per token; LEX_BEARER_STATELESS stops bearer requests from creating session rows.

This session note also records the full-suite run on the complete stack (see suite_tally). Batches: [9h](../../clusters/09-signals_ws/batches.md), [10t](../../clusters/10-api_layer/batches.md).
