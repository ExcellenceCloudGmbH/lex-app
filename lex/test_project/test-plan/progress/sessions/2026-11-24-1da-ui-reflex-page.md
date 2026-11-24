---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 1da 4n 10ag 10d): 176 pass / 0 fail, 237 subtests pass"
---

# lex.ui links: a raw Reflex page as somewhere a link may lead (1da)

A project moving to lex.ui a table at a time keeps its pages in raw Reflex for a while, and its
converted tables could link only to `@ui.page` functions. `ui.reflex_page("/requests/[req]")`
declares such a page as a link target. Batch [1da](../../clusters/01-init/batches.md); 1.783's
namespace gains `reflex_page`.
