---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1dc 4n 10ag 10d): 179 pass / 0 fail, 237 subtests pass"
---

# lex.ui handlers declared on a state mixin are each state's own (1dc)

Reflex copies a mixin's handlers into each state, closure and all, so a lex.ui handler kept the
mixin's key: every state using it shared one running status and one repeat guard. A `ui.State`
now decorates such a handler again under its own name. Batch
[1dc](../../clusters/01-init/batches.md).
