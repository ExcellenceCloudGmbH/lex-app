---
date: 2026-11-10
clusters: [6]
tests_added: "5 new scenarios (6.168-6.172); 6.165 rewritten to the new rule, 6.145 reads as a non-superuser"
suite_tally: "audit_logging 158 pass / 0 fail / 1 xfail (was 153); permissions 148 pass / 2 skip; serializers 58 pass / 1 xfail; api_layer 110 pass; calculation_logging 45 pass / 9 fail / 3 xfail, same 9 failing on untouched v2.4.2 (15b-15e child-node tree, unrelated)"
---

# Batch 6u: who reads a failed calculation's traceback (LEX-1555)

[Batch 6u](../../clusters/06-audit_logging/batches.md). A customer on 2.4.2 found that a failed
calculation's traceback was missing from the audit log. The run still stored it. The read side
was the problem: since 2.4.0 the [6t](../../clusters/06-audit_logging/batches.md) read-surface
hardening had returned `error_traceback` to Django superusers only, and Keycloak users never are
superusers. So in a deployed app nobody saw it, and the frontend's traceback button stayed disabled.

The traceback now follows its entry. Whoever may read the entry (its record, under
`LEX_SEC_READ_SURFACES`) reads it on `auditlog` and `auditlogstatus`, in the lists and by id.
Whoever may not read the record still sees neither row. 6.168-6.170 and 6.172 fail on v2.4.2.
