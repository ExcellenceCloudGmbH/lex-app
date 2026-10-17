---
date: 2026-10-16
clusters: [1, 4, 7]
tests_added: "9 new scenarios (1.396-1.397, 4.111-4.113, 7.240-7.243)"
suite_tally: "1aw 2 pass, 4r 3 pass, 7w 4 pass; each scenario fails against the pre-PR source (checked by swapping the old files in)"
---

# Call-site coverage for the identity and context PR

Follow-up on feat/ext-3-identity-context so that every source file the PR touches has a test of its own (copilot coverage gate). The create_api_key command gets direct tests; the audit-log mixins' author fallback (BUG-007) is checked on single and bulk writes; each of lex's own on_commit call sites (as_transaction, CalculationModel state bookkeeping, CalculationLog) is checked to re-apply DB_CONTEXT, and to stay plain without it.

Batches: [1aw](../../clusters/01-init/batches.md), [4r](../../clusters/04-permissions/batches.md), [7w](../../clusters/07-calculations/batches.md).
