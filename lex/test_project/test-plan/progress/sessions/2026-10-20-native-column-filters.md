---
date: 2026-10-20
clusters: [1, 4, 10, 12]
tests_added: "1 new scenario (10.92); 1.413 replaced (lex_view has no filter_bar); the bar's 10.91-10.93 and 10.95 withdrawn and 10.94 renumbered 10.91; 4.123, 10.25 and 12.35 changed"
suite_tally: "focused (api_layer, init, permissions, queries, serializers, exports): 1081 pass / 7 fail / 16 skip / 1 xfail / 5 collection errors, the 12 failing ids those directories have at 2b1a56aa and on lex-app-v2 at d936dfda (reflex/reflex_enterprise not installed); full suite with this change: 1861 pass / 36 fail / 27 skip / 6 xfail / 5 collection errors (2b1a56aa: 1866 / 36 / 27 / 6 / 5, the same failing and erroring ids: cluster-15 calculation logging, reflex/reflex_enterprise not installed, stress timing, the gate self-test)"
---

# Native column filters — the filter bar is withdrawn (backend)

The grid's own column filters are the only filter UI (spec Amendment 18), so the backend half of the
filter bar goes: `list_ui.filter_bar` (`Meta.filter_bar`, a model's `filter_bar`) and
`lex_view(filter_bar=)`. Value lists stay as they were: `filter_values` with its `search`, the fields
endpoint's `value_list` flag, the GET `__blank` and typed `__exact`/`__in` lookups,
`lex_view(filters=, on_filter=)` and `LEX_EMBED_ALLOWED_ORIGINS`.

[10p](../../clusters/10-api_layer/batches.md) is now the fields endpoint's `value_list` flag (10.91,
was 10.94) and 10.92, which pins that `list_ui` has no `filter_bar` for a model and a serializer that
still declare one; the bar's four scenarios and their fixture are gone and the file is
`test_10p_fields_value_list_flag.py`. [1ay](../../clusters/01-init/batches.md)'s 1.413 now pins that
`lex_view(filter_bar=...)` is a `TypeError`, as for any unknown keyword.
[4r](../../clusters/04-permissions/batches.md)'s 4.123 keeps its `value_list` checks and drops the
bar's. `test_10i`'s 10.25 and `test_12e`'s 12.35 pin lex-app-v2's `list_ui` dicts again.
