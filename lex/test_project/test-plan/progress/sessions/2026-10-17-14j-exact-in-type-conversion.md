---
date: 2026-10-17
clusters: [14]
tests_added: 2
suite_tally: "14j 4 pass (70 pass, full queries dir, final run); api_layer 98 pass, full dir (regression check for the shared base_serializers.py warning dedup, round 2)"
---

# GET `__exact`/`__in` type conversion — C1 final-review fix, and two follow-ups

[14j](../../clusters/14-queries/batches.md) gets a fix-round-2/3 batch: `TestCluster14j_ExactAndInTypeConversion`
(14.82, 14.83) closes C1 from the frontend's final review of `feat/multi-value-filters`, plus two
findings from the scoped re-review that followed it.

**Round 2 (14.82, C1):** the list GET endpoint's `__exact` and `__in` query-param lookups — the
shape a value-list Set filter's "Select All Records" and group-selection fetch send — ran every
value through `_coerce_value` before any field-type logic, for every column, which lowered a
`"null"`/`"none"` spelling to Python `None` and a `"true"`/`"false"` spelling to a bool regardless
of the column's real type. A `CharField`/choice column whose stored key is literally `"none"`
therefore resolved `field__exact=none` to `IS NULL` and lost the key to `None` inside
`field__in=...`. `__exact`/`__in` now convert each value the way the POST Set filter already does
(`_set_filter_values`), via a conversion helper (`_typed_value_converter`/`_coerce_typed_value`)
factored out of that method and shared rather than duplicated. The same change deduplicates
`_resolve_filter_bar`'s "dropped" warning per process per `(serializer class, name)`
(`lex/api/serializers/base_serializers.py`, cluster 10p's file) so a misconfigured `filter_bar`
stops re-logging on every fields-info request; cluster 10p's existing suite (98 pass, full
`api_layer` dir) already covers this path and stayed green.

**Round 2, fail-closed follow-up (still 14.82, sub-cases g/h):** an unconvertible `__exact`/`__in`
value originally made the whole lookup return `None` ("ignore this param"), which fails OPEN --
`?category__in=abc` matched the entire table instead of nothing, and a "Select All" bulk action
would then run over every row. Both branches now return `Q(pk__in=[])` (no rows) when nothing
survives conversion, the same idiom `_build_filter_q` already uses for an explicit empty POST Set
filter.

**Round 3 (14.83, residual):** a re-review of the round-2 diff found that a JSON sub-path through
`__in` (e.g. `?metadata__key__in=5`) now converts via `_typed_value_converter`'s generic
`model_field.to_python`, which for `JSONField` is the inherited identity -- so a numeric/boolean
query value survives unparsed and silently stops matching the differently-typed JSON value actually
stored (it empirically matched the WRONG row -- the one storing the JSON *string* `"5"` -- instead
of matching nothing or the JSON *number* `5` row). A `JSONField` has no single column type to
convert a raw string by (a JSON value's own spelling already says its type), so it now keeps the
legacy, `json.loads`-based `_coerce_value` on `__exact`/`__in` too, for the whole column and any
sub-path alike (`model_field` is the same `JSONField` object either way). The check lives in
`_coerce_typed_value` (GET-only) rather than the shared `_typed_value_converter`, so the POST Set
filter's `_set_filter_values` is untouched.
