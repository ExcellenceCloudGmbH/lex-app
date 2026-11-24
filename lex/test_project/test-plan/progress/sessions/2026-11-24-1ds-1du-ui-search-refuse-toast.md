---
date: 2026-11-24
clusters: [1]
tests_added: 8
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dt 4n 10ag 10d): 216 pass / 0 fail, 405 subtests pass"
---

# lex.ui forms: a choice searched on the server (1ds)

`ui.field(key, options=State.rows_handler)`: one record among thousands, searched on the server as the user types,
with nothing but a page of `{value, label}` matches reaching the browser and the value checked against what the tab
was offered. Batch [1ds](../../clusters/01-init/batches.md).

# lex.ui: a refusal shown above a form's submit button, with a title (1dt)

`raise ui.Refuse(message, title=)` from a form's handler is shown in the form, above its submit button, until the
form is sent again; elsewhere it is a toast headed by its title. Batch [1dt](../../clusters/01-init/batches.md).

