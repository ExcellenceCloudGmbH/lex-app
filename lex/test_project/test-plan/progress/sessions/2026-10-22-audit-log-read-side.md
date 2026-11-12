---
date: 2026-10-22
clusters: [4, 6]
tests_added: "8 new scenarios (6.160-6.167); 4.53 now pins the AuditLogStatus dispatch to its own handler, 6.145 reads the traceback as a superuser"
suite_tally: "audit_logging 153 pass / 0 fail / 1 xfail (was 145); api_layer 107 pass; exports 42 pass; permissions 139 pass / 2 skip; serializers 58 pass / 1 xfail; crud_api 61 pass; history 61 pass / 1 skip / 1 xfail"
---

# Batch 6t — who reads the export and download entries

Follow-up to [6s](../../clusters/06-audit_logging/batches.md). The entries were written
correctly, but read through the create/update/delete rule: a shadow record rebuilt from the
payload. File-operation payloads name no model field, so the shadow was blank — hidden from
everyone, or shown to everyone where a model treats a blank record leniently — and the payload
itself was pruned to `{}`. `auditlogstatus/list` was not filtered at all, with tracebacks.

Now, behind `LEX_SEC_READ_SURFACES`: a download entry follows `permission_read` of its record
(payload check if the record is gone), a model-wide export follows `permission_list` +
`permission_export` on a blank record, superusers see all; the payload is returned whole; status
rows follow their entry; the detail endpoint (`is_readable`) uses the list's rule for the audit
models; tracebacks are superuser-only.

6.160–6.165 and 6.167 fail on the #840 head; 6.166 (opt-out) passes on both.
