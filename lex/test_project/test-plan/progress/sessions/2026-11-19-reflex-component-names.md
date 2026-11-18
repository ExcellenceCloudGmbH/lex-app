---
date: 2026-11-19
clusters: [1]
tests_added: 0
suite_tally: "Reflex batches 1bt, 1bv: 19 pass / 0 fail"
---

# lex-app's Reflex components build in the browser (1.705, 1.723)

Running a project's dashboards in a real browser build -- not the dry-run compile the tests use --
showed every page with `lex_view` or `lex_widgets` failing in Vite: the frame component, and the
theme follower and auth notice, are defined inside cached factories, so their qualified names held
`<locals>`, and Reflex names the JavaScript it generates after them. They are named as if defined
at module level now; 1.705 and 1.723 (batches 1bt and 1bv, see
[batches](../../clusters/01-init/batches.md)) assert the names are valid identifiers.
