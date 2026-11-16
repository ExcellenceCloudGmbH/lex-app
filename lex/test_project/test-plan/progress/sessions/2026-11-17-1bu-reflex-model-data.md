---
date: 2026-11-17
clusters: [1]
tests_added: 10
suite_tally: "Reflex batches 1ap–1at, 1bg, 4n, 1bt, 1bu: 96 pass / 0 fail / 3 skip (environmental)"
---

# Batch 1bu — model data through lex-app's own API

A dashboard's model data now comes through lex-app's REST API as the signed-in user
([batch 1bu](../../clusters/01-init/batches.md), 1.707–1.716): `lex_api` for reads and writes,
`lex_table` over the list endpoint, `lex_grid` over up to 2,000 rows, and `LexRecordState`, which
asks the API for a record before handing out the instance.

The design choice the batch pins: reading through the API rather than the ORM. The grid's
permissions are not one check but several layers — model scopes, row rules in
`permission_read`, field-level filtering, credential-shaped fields the serialisers refuse — and
a dashboard that re-implemented them would drift. Going through the same endpoints means a
dashboard can never show more than the grid.
