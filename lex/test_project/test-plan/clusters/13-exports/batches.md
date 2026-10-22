# Cluster 13 — Export Endpoint · Batch History

> Batch/allocation history for this cluster. Scenario intent lives in
> [`cluster.md`](cluster.md); machine allocation state in
> [`allocation.yaml`](allocation.yaml).

The Export Endpoint batches (13a legacy path, 13b AG-Grid flat, 13c AG-Grid
grouped/selected, 13d auth & edge cases, 13e streaming-cap) were authored in the
cluster definition rather than the retired `test-writing-plan.md`; see
[`cluster.md`](cluster.md) for their scenario-level definitions and
[`allocation.yaml`](allocation.yaml) for the allocation record. No additional
writing-plan batch blocks were recorded for this cluster.

> **Migration note (2026-07-07):** the retired `test-writing-plan.md` carried a
> `## Cluster 13 — Process Admin` block that reused the number 13 for an unrelated,
> never-opened area. It has been moved to
> [`../README.md` §6a](../README.md) as a pending decision — it does not belong here.

---

### Batch 13f — Export datetimes in the requester's browser timezone ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.31 – 13.33 |
| Type | U |
| Files covered | `lex/api/views/file_operations/ModelExport.py` (`_resolve_export_zone`, `_to_excel_naive(zone)`, `ModelExportView._normalize_cell_value` zone rendering, `post` `timezone` param) |
| Test file | `lex/test_project/tests/exports/test_13f_export_timezone.py` |
| Test classes | `TestCluster13f_ExportTimezone` (13.31 Berlin → 11:00; 13.32 New York → 05:00; 13.33 no/invalid tz → settings.TIME_ZONE) both on the legacy pandas path and the streaming/fast path |
| Fixtures | none — pure-function unit test (avoids the export permission-masking confound) |
| Tests landed | **3 pass / 0 fail** |
| Coverage gain | timezone-aware Excel export on both write paths |
| Status | ✅ Complete — Excel has no tz type, so aware-UTC datetimes are baked as a local wall-clock at export time, in the requester's browser zone (frontend sends `timezone` on the export request), falling back to `settings.TIME_ZONE`. Fixes exports showing naive UTC. Ships with the `USE_TZ=True` cutover. |

---

### Batch 13g — Report-file Excel boundary: display-zone wall-clock, both ways ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.34 – 13.37 |
| Type | U |
| Files covered | `lex/core/fields/XLSX_field.py` (`_excel_display_naive`, `XLSXField.create_excel_file_from_dfs` normalization hook) |
| Test file | `lex/test_project/tests/exports/test_13g_xlsx_field_timezone.py` |
| Test classes | `TestCluster13g_XlsxFieldTimezone` (13.34 aware column → Berlin wall-clock naive, incl. winter quarter-end landing on the right calendar day; 13.35 object columns / DatetimeIndex / MultiIndex levels / column headers all normalize, caller's frame untouched; 13.36 end-to-end through `create_excel_file_from_dfs` — the written .xlsx cells read Berlin wall-clock via `pd.read_excel`; 13.37 full round trip — a cell parsed back from the file, assigned to a LexModel `DateTimeField`, becomes aware and denotes the exact original instant) |
| Fixtures | none — in-memory workbook; storage stubbed at the `FieldFile.save` boundary; reuses `FastExportItem.happened_at` for the read-back assignment |
| Tests landed | **4 pass / 0 fail** |
| Coverage gain | the report-file (XLSXField) twin of the 13f export-endpoint timezone rendering |
| Status | ✅ Complete — `to_excel` raises on the aware datetimes every DateTimeField carries under USE_TZ=True, which crashed every report writing through `XLSXField` ("Excel does not support datetimes with timezones"). The boundary now renders aware values as the project display zone's wall clock (settings.TIME_ZONE) and strips tzinfo — the one place where naive is *correct*, because a spreadsheet cell is a wall-clock reading, not an instant. Reading mirrors it: parsed cells are naive local and the 3g assignment invariant restores the instant. |

---

### Batch 13h — Report-file fields: callable from the value, and wide enough ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.38 – 13.45 |
| Type | I |
| Files covered | `lex/core/fields/XLSX_field.py` (`XLSXFieldFile`, `XLSXField.attr_class`, `XLSXField.__init__` max_length default), `lex/core/fields/PDF_field.py` (`PDFField.__init__` max_length default) |
| Test file | `lex/test_project/tests/exports/test_13h_xlsx_field_binding.py` |
| Test classes | `TestCluster13h_XlsxFieldBinding` (13.38 bound `self.report.create_excel_file_from_dfs(...)` writes the workbook and the name reaches the row via the framework's own `model.save()`; 13.39 the unbound `XLSXField.create_excel_file_from_dfs(self.report, ...)` still writes identically; 13.40 the value is still a `FieldFile` — the audit-log serializer dispatches on that type — and the helper carries `alters_data`; 13.41 the value survives the pickle round trip Celery dispatch performs, helper still bound) · `TestCluster13h_LongReportPath` (13.44 a 122-char nested report path round-trips byte-for-byte) · `TestCluster13h_FileFieldMaxLength` (13.42 bare `XLSXField()` is 300 wide; 13.43 an explicit `max_length` still wins; 13.45 bare `PDFField()` likewise) |
| Fixtures | `XlsxReportProbe` in `tests/exports/models.py` — a bare `XLSXField(null=True, blank=True)` + `PDFField(...)`, declared the way downstream apps declare report columns; tables stood up through the cluster tree's `E2ETestCase.e2e_models`; writes land in a throwaway `MEDIA_ROOT` |
| Tests landed | **8 pass / 0 fail** |
| Coverage gain | the field-binding and column-width halves of `XLSX_field.py` / `PDF_field.py`, neither previously exercised (13g covered only the timezone normalization hook) |
| Status | ✅ Complete — two independent traps in one declaration. (1) The helpers are written against a `FieldFile` (`create_excel_file_from_dfs` ends in `self.save(name, content, save=False)`, a FieldFile API) but lived only on the field class, so the spelling customers reach for first raised `AttributeError: 'FieldFile' object has no attribute 'create_excel_file_from_dfs'` inside a Celery worker, naming no code they wrote. `attr_class = XLSXFieldFile` binds the *same function objects*, so both spellings resolve to one implementation and the ~30 unbound call sites across the estate are untouched. (2) `max_length = 300` was dead — `FileField.__init__` setdefaults 100 and `Field.__init__` shadows the class attribute — so every bare `XLSXField()`/`PDFField()` was `varchar(100)`, over which Django's storage *silently truncates and suffixes* rather than raising. **Downstream impact:** apps get one `AlterField` per report column on their next `makemigrations`; `lex_migrate` runs it by default, so the widening lands on deploy. No lex-app model uses either field, so no migration ships in this repo. |

---

### Batch 13i — The export endpoint writes an audit entry ✅

| Property | Value |
| --- | --- |
| Scenario range | 13.46 – 13.51 |
| Type | E |
| Files covered | `lex/api/views/file_operations/ModelExport.py` (`audit_request_payload`, the four response paths of `post`) |
| Test file | `lex/test_project/tests/exports/test_13i_export_audit.py` |
| Test classes | `TestCluster13i_StreamingExportAudit` (13.46 universal streaming → `export_path`, rows, columns, size, model content type and unset `object_id`; 13.47 flat DB-only streaming; 13.48 an export that returns no file → `failure` + status code) · `TestCluster13i_DataFrameExportAudit` (13.49 legacy non-AG path with its sheet name; 13.50 AG-Grid pandas fallback incl. the `filter_model` that decided which rows left; 13.51 a selection recorded as a count, never as the id list) |
| Fixtures | `FastExportItem` (default export permissions → streaming paths) and `ExportItem` (overridden `permission_export` → pandas paths) from `tests/exports/models.py`; `AuditLog` + `AuditLogStatus` in `e2e_framework_models`; Keycloak `export` scope patched for the streaming class |
| Tests landed | **6 pass / 0 fail** |
| Coverage gain | one audited scenario per export writer — the four share no code between request and response, so a green streaming test says nothing about the other three |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-22-export-download-audit.md) |

---
