---
date: 2026-10-18
clusters: [4, 10, 12, 14]
tests_added: "31 new scenarios (4.80-4.100, 12.63, 14.36-14.44); 4.4 live again; 4.41, 10.47-10.54 and the 12h fixture updated to the secure defaults"
suite_tally: "permissions or serializers or queries or api_layer: 318 pass / 0 fail / 2 skip / 1 xfail; crud_api or history or exports or audit_logging or validation_hooks: 338 pass / 0 fail / 1 skip / 2 xfail; stress 11.3 passes, 11.14 still fails (BUG-011 partial)"
---

# Batches 4o, 12n, 14f — v2 security hardening, records part

Branch `fix/v2-security-hardening`, part 1: the permission fixes of #816 ported to lex-app-v2
without the 3.0 extension hooks (`permission_read_queryset`, `permission_serializer`,
`HTMLReport.permission_view`, `lex_strict_input`, `EXPOSE_AUTO_SERIALIZER`, `serialize_for`).
`lex/core/security_flags.py` carries the nine flags (on by default, `LEX_LEGACY_SECURITY`
umbrella, `LEX_SEC_<FLAG>` override) and `is_superuser_or_admin`; `lex/api/security.py` the
shared checks and the 400 refusal body `{code, detail, error}`.

[4o](../../clusters/04-permissions/batches.md) covers fail-closed hooks, 404 for unreadable
records, enforced edit/delete/create rights on every write path (BUG-010 resolved), read
permission on history, search, export, file download and the HTML report endpoint, and that
signed-out callers keep their sign-in answer. [12n](../../clusters/12-serializers/batches.md)
and 4.96 cover BUG-011's duplicate evaluations (partially resolved).
[14f](../../clusters/14-queries/batches.md) is the filter allow-list; 14.43-14.44 keep the
bundled frontend's `ordering=id` and `ag-Grid-AutoColumn` working.

Existing tests that pinned the insecure behaviour now pin the secure one: 4.4 (un-commented),
4.41 (404 by default, `200 {}` under the legacy switch), the 10l file-download mocks, and 12h's
`AttachmentItem` (read/edit opened, the scenarios are about files).
