---
date: 2026-10-16
clusters: [6, 10]
tests_added: "9 new scenarios (6.121-6.122, 10.106-10.112)"
suite_tally: "10s 7 pass, 6s 2 pass; api_layer and audit_logging clusters pass; full-suite result recorded in the PR-5 session note (top of the stack)"
---

# Record and collection actions (@lex_action)

Fourth of five stacked PRs (feat/ext-4-record-actions). Operations that are not CRUD on one model are declared on the model with @lex_action and served at api/model_entries/<model>/<calcId>/one/<pk|uuid>/action/<name> or .../action/<name> for collection actions, with lex's guarantees: visibility (404), a required permission (400 ACTION_NOT_ALLOWED), strict input, one transaction with the caller's audit and database context, an audit entry, and JSON, 204 or file results.

Batches: [6s](../../clusters/06-audit_logging/batches.md), [10s](../../clusters/10-api_layer/batches.md).
