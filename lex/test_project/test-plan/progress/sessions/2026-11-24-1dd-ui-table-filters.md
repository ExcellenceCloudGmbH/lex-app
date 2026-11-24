---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dd 4n 10ag 10d): 180 pass / 0 fail, 237 subtests pass"
---

# lex.ui tables: a date-range filter and a "contains" filter (1dd)

`ui.filter("created", kind="dates")` and `ui.filter("company", kind="text")` sit beside a table's
dropdowns and filter a `@ui.rows` table on the server, only on the fields it offers. Batch
[1dd](../../clusters/01-init/batches.md); 1.783's namespace gains `filter`.
