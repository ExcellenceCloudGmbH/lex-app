---
date: 2026-10-22
clusters: [6, 10, 13, 15]
tests_added: "20 new scenarios (6.121-6.124, 10.91-10.96, 13.46-13.51, 15.52-15.55); 6.96 updated to the extended action vocabulary and 10l's FieldFile stubs completed"
suite_tally: "audit_logging 145 pass / 0 fail / 1 xfail; exports 42 pass / 0 fail; api_layer 107 pass / 0 fail; calculation_logging 45 pass / 9 fail / 3 xfail — the 9 are the cluster's pre-existing cross-test interference, failing identically on a clean tree"
---

# Batches 6s, 10p, 13i, 15l — exports and downloads join the audit trail

Create, update and delete were recorded; export and download were not. Every way data
leaves this framework now writes an `AuditLog` row with the same contract as the write
operations — opened `pending` before the work, finalised to `success` or `failure`.

`AuditLog.ACTION_CHOICES` gains `export` and `download` (migration
`0008_auditlog_file_operation_actions`, choices-only). `audit_logging/utils/file_operation_audit.py`
opens and finalises the entry; `audit_logging/mixins/FileOperationAuditMixin.py` wires it to
DRF, opening the entry in `initial()` (after auth, so the author is the real caller) and
closing it in `finalize_response` / `handle_exception`. Six endpoints declare an action and
their request-side facts: `ModelExport`, `FileDownload`, the three SharePoint views and
`DownloadMarkdownPdf`.

Two decisions worth recording. **A link counts as a download:** a SharePoint share or
preview link hands out the file without streaming a byte, so it is audited as `download`
with a `delivery` discriminator rather than left unrecorded. **A selection is a count:**
an export of ten thousand hand-picked rows records `selected_row_count`, never the id
list, so one export stays one bounded row.

[6s](../../clusters/06-audit_logging/batches.md) pins the shared mechanism;
[10p](../../clusters/10-api_layer/batches.md), [13i](../../clusters/13-exports/batches.md)
and [15l](../../clusters/15-calculation_logging/batches.md) cover the endpoints. 13i asserts
each of `post`'s four writers separately — they share no code between request and response.

Two existing tests changed. 6.96 pinned `ACTION_CHOICES` as exactly create/update/delete;
it now pins the five documented actions, the same drift gate over the new vocabulary.
10l's `SimpleNamespace` stand-ins for a `FieldFile` gained `name` and `size`, which the
download view reads to record what it served.

Not audited: requests rejected before the view runs (no session, no API key). Nothing was
served and no operation began, matching how `AuditLogMixin` treats the write endpoints.
