---
date: 2026-11-25
clusters: [1]
tests_added: 0
suite_tally: "Reflex batches: see the commit"
---

Scenario 1.829 ([1de](../../clusters/01-init/batches.md)) changed: a handler of the same state is handed on like any
other. Refusing it silently broke a real project's search field, suggestions and self-refreshing tables.
