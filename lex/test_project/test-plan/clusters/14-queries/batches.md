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
