---
date: 2026-11-23
clusters: [1]
tests_added: 13
suite_tally: "every Reflex batch (1ap 1aq 1bt 1bu 1bv 1bw 1bx 1by 1bz 1ca 1cb 1cc-1cx 4n 10ag 10d): 172 pass / 0 fail, 234 subtests pass"
---

# lex.ui tables, step 2: rows from a handler, a model narrowed with where= (1cv-1cx)

A table shows rows from a lex model narrowed with `where=`, or from a `@ui.rows` handler: a plain
function on the page's state that returns the rows as a list of dicts. lex-app reads them as the
user, searches, filters, sorts and pages them on the server, and the browser receives only the
visible page, cut down to the fields the table uses. Batches
[1cv to 1cx](../../clusters/01-init/batches.md) cover the handler, the generated table state and
the checks. Batches 1cm (1.783) and 1cr (1.797) were brought in line: the namespace gains
`ui.rows`, `ui.address` and `ui.refresh`, and a table that is not a model now points to
`@ui.rows`. Design: `docs/superpowers/specs/2026-11-23-reflex-ui-tables-design.md`.
