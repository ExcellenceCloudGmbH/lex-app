## Cluster 14 — AG Grid Query Endpoint

## Batch 14f — Value-list filters on the AG Grid list endpoint (14.36-14.41)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14f_value_list_filters.py`
- **Test classes:** `TestCluster14f_ValueListFilters`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 6 pass

## Batch 14g — filter_values: a column's values with counts (14.42-14.70)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/FilterValues.py`, `lex/api/utils/filterable_fields.py`,
  `lex/process_admin/sites/process_admin_site.py`, `lex/api/views/model_entries/List.py` (blank
  folding on plain text columns, fix round 1; Set and Multi on the group level, final review),
  `lex/api/serializers/base_serializers.py` (`readable_field_names` /
  `_resolve_target_and_visible_fields`, fix rounds 1 and 2), `lex/audit_logging/models/AuditLog.py`
  (`lex_value_lists`, final review)
- **Test file:** `lex/test_project/tests/queries/test_14g_filter_values_endpoint.py`
- **Test classes:** `TestCluster14g_FilterValuesEndpoint`, `TestCluster14g_SerializerDependentRefusals`
- **Fixtures:** `QueryItem`, `QueryCategory`, `QueryMaskedItem`, `QueryOpenItem` (fix round 1),
  `QueryDefaultPermissionItem`, `QueryScopedFieldItem` (fix round 2), `QueryItem.summary`
  (a `TextField`), `QueryLedger` / `QueryLedgerEntry` (a `to_field` foreign key) and `AuditLog`
  (final review)
- **Status:** complete — 29 pass (10 original + 12 from fix round 1 + 3 from fix round 2 + 4 from the
  final review, 14.67-14.70; 14.57 widened to a `TextField`)

## Batch 14j — GET list: the `__blank` query-param lookup suffix (14.71-14.72)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14j_blank_query_param.py`
- **Test classes:** `TestCluster14j_BlankQueryParam`
- **Fixtures:** `QueryItem` (`note` — a plain text column; `created_on` — a non-text column)
- **Status:** complete — 2 pass
