---
date: 2026-10-16
clusters: [2, 4, 7, 8, 10]
tests_added: "21 new scenarios (2.117-2.121, 4.104-4.110, 6.7 live again, 7.238-7.239, 8.154-8.156, 10.102-10.105)"
suite_tally: "2m 5 pass, 4q 7 pass, 10r 4 pass, 8d 3 pass, 7v 2 pass, 6c incl. 6.7 pass; crud_api, permissions, api_layer and audit_logging clusters 462 pass / 0 fail; full-suite result recorded in the PR-5 session note (top of the stack)"
---

# Database context hook, scoped API keys, public uuid lookups

Third of five stacked PRs (feat/ext-3-identity-context). DB_CONTEXT tells the database who is asking in every request (one transaction, SET LOCAL), project endpoint, Celery task, background calculation thread and the on_commit callbacks lex registers. Scoped API keys (LexAPIKey) act as a real user with their own scopes and path prefixes; lex create_api_key issues them; API_KEY_HEADERS and API_KEY_RESOLVER adapt them to a project. lex_lookup_field adds uuid routes beside the integer ones. BUG-007 is resolved: a user without names is recorded by username.

Batches: [2m](../../clusters/02-crud_api/batches.md), [4q](../../clusters/04-permissions/batches.md), [7v](../../clusters/07-calculations/batches.md), [8d](../../clusters/08-celery_async/batches.md), [10r](../../clusters/10-api_layer/batches.md).
