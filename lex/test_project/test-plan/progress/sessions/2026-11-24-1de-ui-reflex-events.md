---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1de 4n 10ag 10d): 182 pass / 0 fail, 237 subtests pass"
---

# lex.ui handlers may hand on to another state's handler (1de)

A handler body may return or yield another state's handler (`Other.reload`, `Other.open(key)`);
Reflex's own raw events stay in `@ui.custom`. Batch [1de](../../clusters/01-init/batches.md).
