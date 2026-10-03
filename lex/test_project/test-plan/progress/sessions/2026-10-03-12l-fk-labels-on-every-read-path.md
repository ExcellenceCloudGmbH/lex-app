---
date: 2026-10-03
clusters: [12]
tests_added: 4
suite_tally: "serializers + queries + history: 146 pass / 0 fail / 1 skip / 2 xfail (pre-existing) — 12l 4 pass"
---

# Batch 12l — the FK display name on every request the interface sends

A customer reported foreign keys shown as ids in the v2 frontend, where they expect the related
object's `__str__`. The frontend never fetches the related record to label a cell or a detail
field: it renders the `<fk>__short_description` companion and shows the raw id when the companion
is missing. Batch 12i pinned that companion for the plain GET list and detail, but neither is a
request the screens send. [Batch 12l](../../clusters/12-serializers/batches.md) (12.54–12.57) pins
it on the ones they do send: the grid's AG Grid POST, the History tab's grid on
`historical<model>` (each version naming the FK it held), and the Summary tab's `?serializer=`
request, including a serializer the project declared in `api_serializers`.

All four already pass. They guard the contract rather than expose a bug, so each was
mutation-checked against the code path it covers.

The frontend twins are F3.66 (the hover card's title), F3.67 (the Summary tab) and F8.38 (the
History tab) in process-admin-general-client.
