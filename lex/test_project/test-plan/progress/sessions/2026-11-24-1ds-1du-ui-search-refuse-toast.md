---
date: 2026-11-24
clusters: [1]
tests_added: 4
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1ds 4n 10ag 10d): 212 pass / 0 fail, 401 subtests pass"
---

# lex.ui forms: a choice searched on the server (1ds)

`ui.field(key, options=State.rows_handler)`: one record among thousands, searched on the server as the user types,
with nothing but a page of `{value, label}` matches reaching the browser and the value checked against what the tab
was offered. Batch [1ds](../../clusters/01-init/batches.md).
