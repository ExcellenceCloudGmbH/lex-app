---
date: 2026-11-18
clusters: [1]
tests_added: 0
suite_tally: "Reflex batches 1aq, 1bt, 1bu: 95 pass / 0 fail / 3 skip (environmental)"
---

# A record dashboard written as documented is a state (1.716)

Running a project's record dashboard showed `class FundDashboard(LexRecordState)` -- the shape the
docs, the object-page template and the example all use -- compiled to nothing: `LexRecordState`
was a Reflex mixin, and a class whose only state base is a mixin is a mixin too, so its `load`
never bound to `on_mount`. `LexRecordState` is now a state, and 1.716 (batch 1bu, see
[batches](../../clusters/01-init/batches.md)) also asserts a subclass is a state whose handler binds.
