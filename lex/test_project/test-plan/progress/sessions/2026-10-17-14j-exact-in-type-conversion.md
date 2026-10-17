---
date: 2026-10-17
clusters: [14]
tests_added: 1
suite_tally: "14j 3 pass (69 pass, full queries dir); api_layer 98 pass, full dir (regression check for the shared base_serializers.py warning dedup)"
---

# GET `__exact`/`__in` type conversion — C1 final-review fix

[14j](../../clusters/14-queries/batches.md) gets a fix-round-2 batch: `TestCluster14j_ExactAndInTypeConversion`
(14.73) closes C1 from the frontend's final review of `feat/multi-value-filters`. The list GET
endpoint's `__exact` and `__in` query-param lookups — the shape a value-list Set filter's "Select
All Records" and group-selection fetch send — ran every value through `_coerce_value` before any
field-type logic, for every column, which lowered a `"null"`/`"none"` spelling to Python `None` and
a `"true"`/`"false"` spelling to a bool regardless of the column's real type. A `CharField`/choice
column whose stored key is literally `"none"` therefore resolved `field__exact=none` to `IS NULL`
and lost the key to `None` inside `field__in=...`. `__exact`/`__in` now convert each value the way
the POST Set filter already does (`_set_filter_values`), via a conversion helper
(`_typed_value_converter`/`_coerce_typed_value`) factored out of that method and shared rather than
duplicated; an unconvertible value is dropped instead of ever producing a 400 on a GET read. Bare
keys and every other lookup suffix are untouched. The same change deduplicates
`_resolve_filter_bar`'s "dropped" warning per process per `(serializer class, name)`
(`lex/api/serializers/base_serializers.py`, cluster 10o's file) so a misconfigured `filter_bar`
stops re-logging on every fields-info request; cluster 10o's existing suite (98 pass, full
`api_layer` dir) already covers this path and stayed green.
