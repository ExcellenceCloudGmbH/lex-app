---
date: 2026-11-24
clusters: [1]
tests_added: 4
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dl 4n 10ag 10d): 208 pass / 0 fail, 388 subtests pass"
---

# lex.ui forms: a field where the user chooses several (1dl)

`ui.field(key, kind="choices", options=...)`: RFDS's checkbox group or multi-select, and a `list[str]` in the
order of the options for the handler, checked on the server. Batch [1dl](../../clusters/01-init/batches.md).
Existing tests 1.836 and 1.846 follow: the list of kinds gains `choices`.
