## Cluster 14 — AG Grid Query Endpoint

## Batch 14f — Value-list filters on the AG Grid list endpoint (14.36-14.41)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14f_value_list_filters.py`
- **Test classes:** `TestCluster14f_ValueListFilters`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 6 pass

## Batch 14g — filter_values: a column's values with counts (14.42-14.51)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/FilterValues.py`, `lex/api/utils/filterable_fields.py`,
  `lex/process_admin/sites/process_admin_site.py`
- **Test file:** `lex/test_project/tests/queries/test_14g_filter_values_endpoint.py`
- **Test classes:** `TestCluster14g_FilterValuesEndpoint`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 10 pass
