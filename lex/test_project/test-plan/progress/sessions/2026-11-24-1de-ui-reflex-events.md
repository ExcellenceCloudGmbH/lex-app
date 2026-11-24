---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da-1de 4n 10ag 10d): 181 pass / 0 fail, 237 subtests pass"
---

# lex.ui handlers may send a Reflex event as it is (1de)

A handler body may return or yield another state's handler (`Other.reload`) or an event spec
(`rx.call_script(...)`) among its results; the browser runs it in order. Batch
[1de](../../clusters/01-init/batches.md); the action layer's design is amended in section 6.7.
