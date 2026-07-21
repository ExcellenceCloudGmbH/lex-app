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

### 13f. `_to_excel_naive` — timezone stripping for the legacy Excel path

| # | Scenario | What We Assert |
|---|----------|----------------|
| 13.31 | Empty DataFrame | `_to_excel_naive` returns it unchanged; no exception raised |
| 13.32 | Non-datetime columns (int / float / str) | All cell values pass through unchanged |
| 13.33 | Object column with naive datetime | Value unchanged; `tzinfo` remains `None` |
| 13.34 | Object column with UTC-aware Python datetime | Converted to display-tz wall-clock; `tzinfo` stripped (10:00 UTC → 12:00 Berlin CEST) |
| 13.35 | `DatetimeTZDtype` pandas column | Column dtype becomes timezone-naive; summer + winter offsets both correct |
| 13.36 | Legacy export endpoint with injected tz-aware DataFrame | HTTP 200 + valid xlsx; `xlsxwriter` `ValueError` ("does not support datetimes with timezones") does not fire |

---

- ❌ **Excel formatting cosmetics** (column widths, cell colours,
  freeze panes beyond the default). Those are xlsxwriter's job.
- ❌ **Streaming memory ceiling.** Cluster 11 owns that.
- ❌ **Pivot mode.** The endpoint supports ``pivotMode`` but it is
  not wired to the UI in this framework and is out of scope until a
  customer-visible pivot surface exists.

---
