---
date: 2026-11-24
clusters: [1]
tests_added: 10
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1do 4n 10ag 10d): 218 pass / 0 fail, 422 subtests pass"
---

# lex.ui: progress from a callback (1dm), a text field of several lines (1dn), route arguments named like fields (1do)

`self.progress(fraction, message)` in a `@ui.job`, for a library that reports through a callback on a thread
of its own: shown as `yield ui.progress(...)` is, Stop reaches the library through it, and `progress` is a
reserved name. Batch [1dm](../../clusters/01-init/batches.md). The job state of 1ci renames its field
`progress` to `step`.

`ui.field(key, lines=6)`: RFDS's text area for notes or a pasted list; the handler gets a `str` with its line
breaks, trimmed at the ends; `lines=` goes with text only. Batch [1dn](../../clusters/01-init/batches.md).

The start-up route check refuses a dynamic route argument named like a field or computed var of any state, as
Reflex refuses it only when it builds the frontend. Batch [1do](../../clusters/01-init/batches.md). The raw state of
1de renames its field `key` to `opened`, which collided with 1da's `/orders/[key]`.
