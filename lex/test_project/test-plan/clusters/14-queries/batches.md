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

## Batch 14j — GET list: the `__blank` query-param lookup suffix (14.71-14.73)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14j_blank_query_param.py`
- **Test classes:** `TestCluster14j_BlankQueryParam`, `TestCluster14j_ExactAndInTypeConversion`
  (fix round 2)
- **Fixtures:** `QueryItem` (`note` — a plain text column; `created_on` — a non-text column;
  `category` — a direct FK column, fix round 1; `metadata` — a JSON column, fix round 1; `note`
  holding the literal strings `"none"`/`"true"` and `is_active`, fix round 2), `QueryCategory`
  (fix round 1, reused fix round 2)
- **Status:** complete — 3 pass
- **Fix round 1:** a review found `__blank` wrongly applied to a related path (`category__name__blank`
  built a real filter instead of being ignored, since `field_path` "category__name" has no gap
  against `lookup_path` the way a JSON sub-path does). `_build_query_from_values` now also requires
  `field_path` to carry no `"__"` of its own; 14.72 gained two sub-cases pinning the related-path
  and JSON-sub-path shapes both ignored (200, every row), the latter a regression guard since it
  already passed.
- **Fix round 2 (C1, final review of `feat/multi-value-filters`):** the same GET grammar's `__exact`
  and `__in` lookups ran every value through `_coerce_value` before any field-type logic, for every
  column -- so a value-list Set filter's own stored key `"none"` on a `CharField`/choice column
  (`field__exact=none`, or inside `field__in=...`) was silently lowered to Python `None` and
  resolved to `IS NULL` instead of matching the row that literally stores it, and the same key
  inside `__in` converted to `None` and dropped out. 14.73 (`TestCluster14j_ExactAndInTypeConversion`)
  pins that `__exact`/`__in` now convert each value by the column's own type -- a foreign key's
  target field's `to_python`, a boolean's `_bool_from_key`, the field's own `to_python` otherwise --
  via `_typed_value_converter`/`_coerce_typed_value`, a conversion helper factored out of and shared
  with the POST Set filter's `_set_filter_values` rather than duplicated. A value that fails this
  conversion is dropped (the whole param ignored if none survive), never a 400, since a GET list
  request stays a read. Bare keys (no lookup suffix) and every other lookup keep the legacy,
  type-blind `_coerce_value` unchanged, so `?field=null` still means `IS NULL` for existing callers.
  `_resolve_filter_bar` (`lex/api/serializers/base_serializers.py`, cluster 10o) also gained a
  per-process per-(serializer class, name) dedup on its "dropped" warning in the same change, so a
  misconfigured `filter_bar` no longer re-logs on every fields-info request.
