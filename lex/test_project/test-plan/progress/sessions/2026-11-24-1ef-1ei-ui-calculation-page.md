---
date: 2026-11-24
clusters: [1]
tests_added: 18
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea-1ei 4n 10ag 10d): 276 pass / 0 fail, 466 subtests pass"
---

# lex.ui calculation pages: a calculation model's page (1ef-1ei)

`ui.calculation(Model, title=, fields=/steps=, result=, history=)`: the form for a run's inputs, created
as a record and started through lex-app's API as the user; the same page with `?run=<key>` follows the
run until it settles -- in lex-app's process with Celery off, on a worker with Celery on -- and shows
its result (written with the model's own fields, `RegionReport.orders`), its error in its own words,
its log and its file; the past runs as a model table; Stop as lex-app's cancel. New on `self.api`:
`calculate`, `log`, `file`. Design: section 6.19 of
`docs/superpowers/specs/2026-11-23-reflex-ui-pages-design.md`. Batches
[1ef, 1eg, 1eh, 1ei](../../clusters/01-init/batches.md). The tests run the page's handlers in
`ui.testing.Harness` against lex-app's real views through the signed-in `APIClient`
(`tests/init/_ui_calc_aids.py`).
