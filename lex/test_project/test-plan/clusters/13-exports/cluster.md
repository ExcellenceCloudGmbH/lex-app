## 13. Export Endpoint

**What it tests:** the real ``POST /api/<model>/export`` endpoint —
the one the AG Grid UI hits when a customer clicks *Export to
Excel*. Cluster 11 benchmarks the ORM-level export *pattern* (one
SQL with ``select_related`` + ``.iterator()``); this cluster tests
the **HTTP endpoint** that wraps it, plus all 17 helper methods
inside :class:`ModelExportView` that translate an AG Grid payload
into an ``.xlsx`` file.

**Why it matters:** export is the single most user-visible feature
of the framework that has **zero E2E coverage** today. The existing
tests in ``lex/tests/unit/grid/`` call a few utility methods in
isolation; they don't drive the ``post()`` entry point and they
never exercise the grouped / selection / FK-display paths. That's
how we ended up with 17 methods that no test touches even though
we have a cluster named *FK-heavy export*.

**Why last:** every feature it depends on (CRUD, permissions,
history, FK relations, AG Grid filter/sort pipeline) must already
be green. The export endpoint is the wide-surface integration
point that wires them all together into a single binary artefact.

**Design principle:** tests are **scenario-driven, not method-
driven**. A single "AG grouped export with FK display names"
scenario fires through ``post`` → ``_normalize_ag_request`` →
``_build_ag_grid_dataframe`` → ``_collect_ag_export_rows`` →
``_apply_export_mask_to_ag_rows`` → ``_refresh_hierarchy_labels_
with_readable_values`` → ``_apply_foreign_key_display_names`` →
``_apply_ag_column_layout`` in one shot. We assert on the
**returned .xlsx file contents** using ``pandas.read_excel``, not
on internal state.

**Models needed** (dedicated — same rule-#3 discipline as the other
clusters):

- ``ExportCategory`` — FK target. ``__str__`` returns
  ``f"Cat<{name}>"`` so FK-display-name assertions have a clear,
  non-default expected value.
- ``ExportItem`` — ``LexModel`` with ``name`` / ``amount``
  (Decimal) / ``status`` (choice) / ``category`` (FK). Default
  ``permission_export`` so the uniform fast path fires.
- ``ExportMaskedItem`` — same shape, but ``permission_export``
  returns ``allow_fields({"id", "name"})`` for non-admins. Used to
  lock in the field-level export mask contract.

### 13a. Legacy (non-AG) export path

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.1 | Empty queryset → 404 | Body ``{"error": "No data available for export"}``; no ``.xlsx`` bytes |
| 13.2a | 5 rows, default permissions, no FK | HTTP 200 + ``.xlsx`` body; row count matches DB; every flat row is populated |
| 13.2b | 5 rows, default permissions, with FK | HTTP 200 + ``.xlsx`` body; row count matches DB; FK column shows ``str(category)`` (**``Cat<...>``**), not the integer pk |
| 13.3 | ``filtered_export`` base64-encoded id list | Only the selected ids are in the exported sheet (legacy path routes through ``PrimaryKeyListFilterBackend.filter_for_export``) |
| 13.4 | ``permission_export`` restricts fields | Restricted columns are present in the sheet but blank; allowed columns carry values |

### 13b. AG Grid export path — flat

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.5 | AG flat with ``columns`` payload | Fast path fires (``_try_build_flat_fast_export_dataframe`` returns a non-None df); exported columns are in the requested order with ``headerName`` applied; FK column shows readable name |
| 13.6 | AG ``columns`` including ``short_description`` | Computed field silently skipped by ``_resolve_export_field_paths``; fast path still succeeds; other requested columns still in the sheet |
| 13.7 | AG ``endRow`` over ``MAX_AG_EXPORT_ROWS`` | Clamped silently; export does not raise |

### 13c. AG Grid export path — grouped & selected

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.8 | AG ``rowGroupCols`` with 2 levels | Sheet contains group-header rows with indented ``__ag_group_hierarchy_label`` and a recorded group depth (Excel outline level); ``_collect_ag_export_rows`` recursion visits both levels |
| 13.9 | AG ``selection.groupKeyPaths`` filter | Only rows matching the selected group key are in the sheet; ``_coerce_group_key`` converts ``"1"`` → ``int(1)`` for an integer FK and ``"null"`` → ``__isnull=True`` for a null FK |
| 13.10 | AG payload + base64 ``filtered_export`` | ``_extract_selected_ids_for_export`` path taken; only the decoded ids in the sheet (this is the AG-path analogue of 13.3) |

### 13d. Auth & edge cases

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.11 | Unauthenticated POST | 401 / 403; no ``.xlsx`` bytes |
| 13.12 | Per-object ``permission_export`` (non-uniform) | ``_compute_uniform_export_mask`` returns ``None``; slow per-row mask runs; each row's columns are masked according to that row's own permission result |

### 13h. Report-file fields — binding and column width

Not the export *endpoint* but the export *field*: the `XLSXField` / `PDFField`
columns downstream apps declare and write from `calculate()`.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.38 | Bound write — `self.report.create_excel_file_from_dfs(...)` | Workbook lands in storage and the filename reaches the row via the framework's own `model.save()` (the helper saves with `save=False`) |
| 13.39 | Unbound write — `XLSXField.create_excel_file_from_dfs(self.report, ...)` | The historical form every Lex app uses writes identically; binding the value must not cost the class form |
| 13.40 | Value identity | Still an `isinstance` `FieldFile` (the audit-log serializer dispatches on it), specifically `XLSXFieldFile`, and the helper carries Django's `alters_data` marker |
| 13.41 | Pickle round trip | Survives the serialization Celery dispatch performs — `calc_and_save` receives model instances over the broker, so a class that does not pickle fails only in the worker |
| 13.42 | Bare `XLSXField()` width | `max_length` is 300, the width the class advertises — not Django's `FileField` default of 100 |
| 13.43 | Explicit `max_length` | The caller's value wins; the default stays a default |
| 13.44 | 122-char nested report path | Round-trips byte-for-byte; over the column width Django's storage silently truncates and suffixes rather than raising |
| 13.45 | Bare `PDFField()` width | Same 300 default — PDF reports build the same nested paths |

### 13i. The export endpoint writes an audit entry

An export hands model data out of the system, so it is recorded like a
create / update / delete — see [[history-and-audit/audit logs]]. ``post``
reaches the file through four machines that share no code between request
and response, so each is asserted on its own.

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.46 | Universal streaming export | One ``AuditLog`` row, ``action='export'``, ``resource`` and content type naming the model, ``object_id`` unset (a model-wide export touches no single record), ``export_path='universal-stream'``, the rows and columns actually written and the file's size |
| 13.47 | Flat DB-only streaming export | Same facts under ``export_path='flat-stream'`` — the universal path's coverage says nothing about this writer |
| 13.48 | Export with no rows | The entry still exists, status ``failure``, carrying the 404 — a refused export is an attempt worth recording |
| 13.49 | Legacy non-AG export | ``export_path='legacy'``, ``mode='legacy'``, row count and the worksheet the data was written to |
| 13.50 | AG-Grid pandas fallback | ``export_path='pandas'`` and the ``filter_model`` that decided which rows left the system |
| 13.51 | Hand-picked selection | ``mode='filtered'`` with ``selected_row_count``; the id list itself never enters the payload, so a ten-thousand-row selection stays one bounded entry |

### 13j. Value-list filters in the export

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.52 | Set filter with blank, and an empty selection | On the pandas and the streaming export path the sheet holds exactly the rows the list POST returns for the same filter; nothing ticked exports no rows (404 "No data available for export") |

**Scenario range:** 13.52. **Test file:** `lex/test_project/tests/exports/test_13j_value_list_filters.py`. **Type:** E. **Status:** ✅ Complete (2026-10-16). Batch: [13j](batches.md).

**What is explicitly NOT tested here:**

- ❌ **Excel formatting cosmetics** (column widths, cell colours,
  freeze panes beyond the default). Those are xlsxwriter's job.
- ❌ **Streaming memory ceiling.** Cluster 11 owns that.
- ❌ **Pivot mode.** The endpoint supports ``pivotMode`` but it is
  not wired to the UI in this framework and is out of scope until a
  customer-visible pivot surface exists.

---
