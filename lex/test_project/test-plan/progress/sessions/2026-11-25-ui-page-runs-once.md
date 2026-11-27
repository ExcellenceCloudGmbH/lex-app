---
date: 2026-11-25
clusters: [1]
tests_added: 1
suite_tally: "Reflex batches: 384 pass / 0 fail"
---

lex.ui ran every `@ui.page` function twice per start: once when lex-app took the declared pages (to check them)
and again when the app compiled the page. That was twice the work, and page code that registers something for the
process failed the second time (RFDS's hotkeys, keyed by a translated label). The build checked at the start is now
the one the app compiles first. Scenario 1.1123 ([1db](../../clusters/01-init/batches.md)).
