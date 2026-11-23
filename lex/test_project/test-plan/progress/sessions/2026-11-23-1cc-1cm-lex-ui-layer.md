---
date: 2026-11-23
clusters: [1]
tests_added: 44
suite_tally: "every Reflex batch (1ap 1aq 1bt 1bu 1bv 1bw 1bx 1by 1bz 1ca 1cb 1cc-1cm 4n 10ag 10d): 131 pass / 0 fail, 115 subtests pass"
---

# The lex.ui action and state layer (1cc-1cm)

Reflex handlers are written as plain synchronous functions on a `ui.State`, under `@ui.load`,
`@ui.action` and `@ui.job`; lex-app runs the body off the event loop and supplies the user, the API
as that user, permission checks, status, discard on failure and the display of failures. Batches
[1cc to 1cm](../../clusters/01-init/batches.md) cover each module. The scaffold templates and the
reference example are written on the layer. Design:
`docs/superpowers/specs/2026-11-23-reflex-ui-action-state-layer-design.md`.
