---
date: 2026-10-23
clusters: [4]
tests_added: "5 new scenarios (4.111-4.115); 4.98 lists the tenth flag"
suite_tally: "permissions 143 pass / 2 skip (was 138 / 2); crud_api 61, validation_hooks 39, api_layer 110, audit_logging 145 / 1 xfail -- all as on lex-app-v2 0ac01da8"
---

# Batch 4q — permission_create on the filled record

See [4q](../../clusters/04-permissions/batches.md). The create endpoint asked `permission_create`
only on a blank record, so a rule that depends on the parent or a value had to be re-checked in
each project's `pre_validation`. Behind the new flag `LEX_SEC_CREATE` (on by default) it is asked
again on the record carrying the request's validated values, before the audit entry and
`pre_validation`; the refusal shape is the blank check's 401/403 `{message}`.

4.111–4.114 fail on lex-app-v2 `0ac01da8`; 4.115 (opt-out) passes on both.
