---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da 1dg 4n 10ag 10d): 177 pass / 0 fail, 237 subtests pass"
---

# Reflex config: the npm packages RFDS's browser code needs (1dg)

A lex.ui table's clearable search box loads RFDS's `rfds_form.js`, which imports
`react-number-format`; RFDS declares that package only on its form and number inputs, so a project
using neither could not build. `lex_config()` now installs it and keeps a project's own frontend
packages beside it. Batch [1dg](../../clusters/01-init/batches.md).
