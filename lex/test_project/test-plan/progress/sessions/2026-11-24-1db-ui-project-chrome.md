---
date: 2026-11-24
clusters: [1]
tests_added: 2
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da 1db 4n 10ag 10d): 178 pass / 0 fail, 237 subtests pass"
---

# lex.ui's part of lex-app's app, and every page in lex-app's chrome (1db)

`create_app` hands lex.ui's pages to the app in one internal step. A project-frame hook
(`ui.chrome`, a public `ui.install`) was built first and withdrawn after an independent review: it
followed one ported project's habits. Batch [1db](../../clusters/01-init/batches.md).
