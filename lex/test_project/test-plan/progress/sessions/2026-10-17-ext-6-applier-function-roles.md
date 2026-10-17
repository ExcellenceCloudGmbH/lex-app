---
date: 2026-10-17
clusters: [5]
tests_added: "2 new scenarios (5.130-5.131)"
suite_tally: "5p 2 pass (PostgreSQL); 1ax unchanged and green"
---

# Who may run the bitemporal applier's SECURITY DEFINER functions

On feat/ext-6-project-fit. lex_apply_due_activations() and its siblings run with the owner's rights and PostgreSQL lets every role execute them, so a restricted application role could write past row-level security. lex_config.BITEMPORAL_FUNCTION_ROLES names the roles that may run them; after every migrate lex revokes EXECUTE from PUBLIC and grants it to those roles (the owner keeps it). Unset: no change, because the pg_cron job may run as a server admin that is not a superuser.

Batch: [5p](../../clusters/05-history/batches.md).
