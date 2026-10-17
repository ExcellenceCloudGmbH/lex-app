---
date: 2026-10-16
clusters: [1, 4, 10, 13, 14]
tests_added: 85
suite_tally: "14f 6 pass, 14g 29 pass, 14j 2 pass, 4o 8 pass, 10o 5 pass, 13i 1 pass, 1aw 14 pass, 1ax 20 pass; full suite 1736 pass / 37 fail / 27 skip / 6 xfail / 5 collection errors, every failure in the baseline categories (cluster-15 calculation_logging, reflex/reflex_enterprise not installed, stress timing, the gate self-test)"
---

# Multi-value filters — backend and Streamlit

Value-list filters for the grid and its embeds: a column filter lists its values with a count each,
narrowed by every other active filter.

[14f](../../clusters/14-queries/batches.md) converts a Set filter's keys with the column's own type,
folds blank to one key — including `""` on plain text — and makes an empty selection match no rows,
the same way in the list, the export and group-level requests. [14g](../../clusters/14-queries/batches.md)
adds `filter_values`: it facets a column's own values against every other active filter, counts them,
labels choices, foreign keys, booleans and blanks, and bounds search and page size.
[4o](../../clusters/04-permissions/batches.md) proves a value list never counts a row or a field the
user cannot read, on both the all-or-nothing database path and the per-row permission path, through
the full bitemporal history chain and for API-key requests the same as session ones.
[10o](../../clusters/10-api_layer/batches.md) adds `list_ui.filter_bar` and the fields endpoint's
per-field `value_list` flag, both decided by the same filterable-field resolver `filter_values` uses.
[13i](../../clusters/13-exports/batches.md) pins that an export taken under a Set filter holds exactly
the rows the list shows, on both the pandas and the streaming export paths.
[1aw](../../clusters/01-init/batches.md) adds `lex_view(filters=, filter_bar=, on_filter=)`: every
frame load re-syncs the host's filters, and a filter token too long for the URL is delivered by
message instead.
[14j](../../clusters/14-queries/batches.md) adds a `__blank` lookup suffix to the list GET endpoint,
so "Select All Records" — which fetches ids through GET, not POST — matches blank the same way
`_build_filter_q`'s Set branch already does.
[1ax](../../clusters/01-init/batches.md) adds `LEX_EMBED_ALLOWED_ORIGINS`, so only operator-named
sites can embed lex-app and use the filter bridge at all: a validated settings parser, a CSP
`frame-ancestors` directive the embed middleware merges into every response it already exempts, and
the `config.js` value the frontend reads to gate its own `filter` channel.
