---
date: 2026-10-23
clusters: [4]
tests_added: "5 new scenarios (4.116-4.120)"
suite_tally: "permissions 143 pass / 2 skip (was 138 / 2); audit_logging 153 / 1 xfail, api_layer 110, exports 42, serializers 58 / 1 xfail, crud_api 61, history 61 / 1 skip / 1 xfail -- as on fix/audit-log-read-side-v2"
---

# Batch 4r — a raising permission hook denies on the remaining surfaces

See [4r](../../clusters/04-permissions/batches.md). 4o made a raising hook deny on the record
endpoints; the audit-log list and payload, the export's per-row field selection and
`PermissionAwareSerializerMixin` still allowed. Under `LEX_SEC_FAIL_CLOSED` they deny now; the
opt-out restores the allow. Based on the re-land of #842, which rewrote the neighbouring
audit-log read code in `lex/api/utils/helpers.py`. All five scenarios fail without the change.
