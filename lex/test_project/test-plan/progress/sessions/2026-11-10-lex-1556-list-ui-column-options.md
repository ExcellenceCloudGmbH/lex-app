---
date: 2026-11-10
clusters: [10, 12]
tests_added: "12 new scenarios (10.184-10.187, 12.66-12.72); 10.25 and 12.35 stop asserting that `list_ui` has nothing else in it"
suite_tally: "serializers + api_layer: 180 pass / 0 fail / 1 xfail"
---

# Batches 10ad + 12p — a project declares how its columns look

See [10ad](../../clusters/10-api_layer/batches.md) and
[12p](../../clusters/12-serializers/batches.md).

LEX-1556 (Lund Consulting, via support): the grid's ID column is too wide. The only answer
the framework had was "resize it and save a view" — which a read-only System View does not
keep, which has to be redone per model, and which says nothing to the many users who never
read the identifier at all. The reporter's own suggestion was that the fix belongs in
project code ("the project code moves ID to the very right with a serialiser"), and that is
very nearly what this is: the frontend already recognises three owners of how a table looks
— the individual user, a saved view, and **the developer** — but the developer's layer
(`list_ui`) could say exactly one thing, `hide_actions_column`, and nothing about a column.

`Meta.lex_column_options` is the missing layer: `width` / `min_width` / `max_width` /
`pinned` / `hidden`, per field, validated into `list_ui.columns`.

Nothing in that path may raise. It is read while answering `model_info`, which the entire
frontend blocks on before it can draw any table, so a project that mistypes an option gets
that option ignored rather than a grid that will not load — which is why most of 12p is
about the garbage and only 12.66 is the happy path.

Two surfaces that are easy to miss, and each got a scenario rather than an assumption:
`create_list_ui_info` serves a project's **plain DRF serializer** as well as a
`LexSerializer` (10.185), and a hand-written `ModelSerializer` registered as
`api_serializers["default"]` is **wrapped** before use (12.72) — the path a project that
cares about column widths is most likely to be on.

The frontend half is clusters F03p (the translation and the framework's own identifier cap)
and F03q (a real grid, including a model whose serializer declares these options).
