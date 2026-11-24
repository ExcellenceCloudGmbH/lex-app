---
date: 2026-11-24
clusters: [1]
tests_added: 7
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dn 4n 10ag 10d): 215 pass / 0 fail, 414 subtests pass"
---

# lex.ui: progress from a callback (1dm), a text field of several lines (1dn)

`self.progress(fraction, message)` in a `@ui.job`, for a library that reports through a callback on a thread
of its own: shown as `yield ui.progress(...)` is, Stop reaches the library through it, and `progress` is a
reserved name. Batch [1dm](../../clusters/01-init/batches.md). The job state of 1ci renames its field
`progress` to `step`.

`ui.field(key, lines=6)`: RFDS's text area for notes or a pasted list; the handler gets a `str` with its line
breaks, trimmed at the ends; `lines=` goes with text only. Batch [1dn](../../clusters/01-init/batches.md).
