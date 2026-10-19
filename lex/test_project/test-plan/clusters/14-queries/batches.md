## Cluster 14 — AG Grid Query Endpoint

### Batch 14f — Filter and ordering allow-list ✅

| Property | Value |
| --- | --- |
| Scenario range | 14.36 – 14.44 |
| Type | E |
| Files covered | `lex/api/filter_policy.py`, `lex/api/views/model_entries/List.py` (`apply_query_param_filters`, `apply_ordering`, `_is_valid_field_path`) |
| Test file | `lex/test_project/tests/queries/test_14f_filter_allow_list.py` |
| Test classes | `TestCluster14f_FilterAllowList` |
| Fixtures | `PolicyOwner`, `PolicyItem`, `PolicyOpenItem` (new in `queries/models.py`, with explicit `api_serializers`) |
| Tests landed | **9 pass / 0 fail** |
| Status | ✅ Complete — ported from #816; 14.43–14.44 added for the bundled frontend |

---

## Batch 14k — Value-list filters on the AG Grid list endpoint (14.45-14.50)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14k_value_list_filters.py`
- **Test classes:** `TestCluster14k_ValueListFilters`
- **Fixtures:** `QueryItem`, `QueryCategory`
- **Status:** complete — 6 pass

## Batch 14g — filter_values: a column's values with counts (14.51-14.79)

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
  final review, 14.76-14.79; 14.66 widened to a `TextField`)

## Batch 14j — GET list: the `__blank` query-param lookup suffix (14.80-14.83)

- **Type:** E
- **Files covered:** `lex/api/views/model_entries/List.py`
- **Test file:** `lex/test_project/tests/queries/test_14j_blank_query_param.py`
- **Test classes:** `TestCluster14j_BlankQueryParam`, `TestCluster14j_ExactAndInTypeConversion`
  (fix rounds 2-3)
- **Fixtures:** `QueryItem` (`note` — a plain text column; `created_on` — a non-text column;
  `category` — a direct FK column, fix round 1; `metadata` — a JSON column, fix round 1; `note`
  holding the literal strings `"none"`/`"true"` and `is_active`, fix round 2; `metadata` holding a
  JSON number/string/boolean/empty object under the same key, fix round 3), `QueryCategory`
  (fix round 1, reused fix round 2)
- **Status:** complete — 4 pass
- **Fix round 1:** a review found `__blank` wrongly applied to a related path (`category__name__blank`
  built a real filter instead of being ignored, since `field_path` "category__name" has no gap
  against `lookup_path` the way a JSON sub-path does). `_build_query_from_values` now also requires
  `field_path` to carry no `"__"` of its own; 14.81 gained two sub-cases pinning the related-path
  and JSON-sub-path shapes both ignored (200, every row), the latter a regression guard since it
  already passed.
- **Fix round 2 (C1, final review of `feat/multi-value-filters`):** the same GET grammar's `__exact`
  and `__in` lookups ran every value through `_coerce_value` before any field-type logic, for every
  column -- so a value-list Set filter's own stored key `"none"` on a `CharField`/choice column
  (`field__exact=none`, or inside `field__in=...`) was silently lowered to Python `None` and
  resolved to `IS NULL` instead of matching the row that literally stores it, and the same key
  inside `__in` converted to `None` and dropped out. 14.82 (`TestCluster14j_ExactAndInTypeConversion`)
  pins that `__exact`/`__in` now convert each value by the column's own type -- a foreign key's
  target field's `to_python`, a boolean's `_bool_from_key`, the field's own `to_python` otherwise --
  via `_typed_value_converter`/`_coerce_typed_value`, a conversion helper factored out of and shared
  with the POST Set filter's `_set_filter_values` rather than duplicated. A value that fails this
  conversion is dropped, and if NONE of the given values survive the lookup matches no rows
  (`Q(pk__in=[])`, the same idiom `_build_filter_q` already uses for an explicit empty POST Set
  filter) rather than ever becoming a 400 or silently matching every row -- returning "no filter"
  there would fail open (an unconvertible `?category__in=abc` would otherwise match the whole
  table, and a "Select All" bulk action would then run over everything). 14.82(g)/(h) pin this.
  Bare keys (no lookup suffix) and every other lookup keep the legacy, type-blind `_coerce_value`
  unchanged, so `?field=null` still means `IS NULL` for existing callers.
  `_resolve_filter_bar` (`lex/api/serializers/base_serializers.py`, cluster 10p) also gained a
  per-process per-(serializer class, name) dedup on its "dropped" warning in the same change, so a
  misconfigured `filter_bar` no longer re-logs on every fields-info request.
- **Fix round 3 (residual, same re-review):** a `JSONField` is the one exception to "every other
  field type stays typed" (fix round 2) -- it keeps the legacy `_coerce_value` on `__exact`/`__in`
  too, for the whole column and for any sub-path through it, because a JSON value's own spelling
  already says its type (`5` is a number, `"5"` a string, `true` a bool, `null` is null); there is
  no single column type to convert a raw query string by. Before this fix a JSON sub-path through
  `__in` (e.g. `?metadata__key__in=5`) fell back to `_typed_value_converter`'s generic
  `model_field.to_python`, which for `JSONField` is the inherited identity -- so the raw string
  `"5"` matched the row storing the JSON **string** `"5"` instead of the row storing the JSON
  **number** `5`. 14.83 pins the sub-path (a) number, (b) boolean and (c) whole-column shapes on
  `__in`, plus (d) a sub-path `__exact` regression guard -- that one already fell through to the
  legacy path untouched, since the `__exact` branch's path-equality guard excludes a JSON sub-path
  on its own (`"metadata__key__exact" != "metadata__exact"`). The check lives in
  `_coerce_typed_value` (GET-only), not in the shared `_typed_value_converter`, since `model_field`
  is the same `JSONField` object for the whole column and for a sub-path alike --
  `_resolve_lookup`'s JSON branch never manufactures a separate `Field` for a key.

### Batch 14l — Value lists and the GET lookups under lex-app-v2's filter allow-list ✅

| Property | Value |
| --- | --- |
| Scenario range | 14.84 – 14.86 |
| Type | E |
| Files covered | `lex/api/serializers/base_serializers.py` (`_keeps_lex_model_can_read`, `_resolve_target_and_visible_fields`), `lex/api/views/model_entries/FilterValues.py` (inherited `_apply_filter_model`), `lex/api/views/model_entries/List.py` (`apply_query_param_filters`, `_build_query_from_values`), `lex/api/filter_policy.py` |
| Test file | `lex/test_project/tests/queries/test_14l_value_lists_and_the_filter_allow_list.py` |
| Test classes | `TestCluster14l_ValueListsAndTheFilterAllowList` |
| Fixtures | `QueryOpenItem` (14g), `PolicyItem` (14f); `LEX_SEC_*`/`LEX_LEGACY_SECURITY` cleared per test |
| Tests landed | **3 pass / 0 fail** |
| Also changed | 14.75 pins grid parity for an API-key caller instead of the bypass this branch's fast path had |
| Status | ✅ Complete — see [session note](../../progress/sessions/2026-10-19-merge-lex-app-v2-secure-defaults.md) |

---
