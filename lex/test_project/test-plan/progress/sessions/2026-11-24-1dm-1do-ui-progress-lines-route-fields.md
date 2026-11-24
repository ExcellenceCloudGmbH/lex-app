---
date: 2026-11-24
clusters: [1]
tests_added: 4
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dm 4n 10ag 10d): 212 pass / 0 fail, 393 subtests pass"
---

# lex.ui: progress from a callback (1dm)

`self.progress(fraction, message)` in a `@ui.job`, for a library that reports through a callback on a thread
of its own: shown as `yield ui.progress(...)` is, Stop reaches the library through it, and `progress` is a
reserved name. Batch [1dm](../../clusters/01-init/batches.md). The job state of 1ci renames its field
`progress` to `step`.
