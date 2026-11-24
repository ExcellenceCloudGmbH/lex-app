---
date: 2026-11-25
clusters: [1]
tests_added: 2
suite_tally: "Reflex batches: see the commit"
---

A `@ui.rule`, a function of the user given to `ui.frame` and `ui.frame(opened=)` run on worker threads outside a
handler body, where `user.permissions` raised. lex-app now reads the user's permissions on the event loop first and
hands them over with the user. Scenarios 1.1090 ([1ed](../../clusters/01-init/batches.md)) and 1.1091
([1ee](../../clusters/01-init/batches.md)).
