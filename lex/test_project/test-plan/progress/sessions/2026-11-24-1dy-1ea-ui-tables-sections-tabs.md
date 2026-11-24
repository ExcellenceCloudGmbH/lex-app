---
date: 2026-11-24
clusters: [1]
tests_added: 13
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dz 1ea 4n 10ag 10d): 221 pass / 0 fail, 394 subtests pass"
---

# lex.ui tables: columns and rows a reader relies on (1dy)

`ui.column(kind="name"|"code", pin=True)` and `ui.table(choose_columns=True, page_size=None,
totals={"amount": "sum of all rows"})`: RFDS's sticky column, field widths, column chooser,
pagination and totals. Batch [1dy](../../clusters/01-init/batches.md).

# lex.ui pages: a section closed until the user opens it (1dz)

`ui.section(title, ..., collapsed=True)`: RFDS's section as a native disclosure, its title counting its table's
rows. Batch [1dz](../../clusters/01-init/batches.md).

# lex.ui pages: a tool's result as tabs (1ea)

`ui.form(..., tabs=True)`, the record page's argument where sections also are: the result's sections as RFDS's
tabs. Batch [1ea](../../clusters/01-init/batches.md).
