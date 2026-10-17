## Cluster 14 — AG Grid Query Endpoint

(no batches recorded yet)

### Batch 14f — Filter and ordering allow-list ✅

| Property | Value |
| --- | --- |
| Scenario range | 14.36-14.42 |
| Type | E |
| Files covered | `lex/api/filter_policy.py`, `lex/api/views/model_entries/List.py` (apply_query_param_filters, apply_ordering, _is_valid_field_path) |
| Test file | `lex/test_project/tests/queries/test_14f_filter_allow_list.py` |
| Test classes | `TestCluster14f_FilterAllowList` |
| Fixtures | `PolicyOwner`, `PolicyItem` (FK with limit_choices_to), `PolicyOpenItem` (lex_filterable_fields='__all__'), each with an explicit api_serializers default |
| Tests landed | **7 pass / 0 fail** |
| Status | ✅ Complete |
| Note | Framework models (audit log, audit status, calculation log, history tables) are exempt because the bundled frontend filters them by internal fields. |
