---
date: 2026-11-10
clusters: [15]
tests_added: "7 new scenarios (15.56-15.62), batch 15m"
suite_tally: "calculation_logging: 52 pass / 9 fail / 3 xfail. The 9 failures (15b, 15c, 15d, 15e: calc_and_save_sync pipeline) fail identically on an untouched lex-app-v2 baseline and do not touch the PDF view. 15h + 15l + 15m: 18 pass / 0 fail"
---

# LEX-1554: the calculation-log PDF covers one step, a branch, or the whole calculation

See [15m](../../clusters/15-calculation_logging/batches.md). The customer could download the
calculation tree only one node at a time and asked for the whole tree, and for a node with
everything under it, in one PDF, from both download buttons. `GET api/download-pdf/<pk>/` now takes
`?scope=node|subtree|calculation`. `calculation` renders every log of the calculation the node
belongs to: the Execution tree's rows, every root titled when there are several. The frontend's
shared download menu sends it from the tree header and from the Details "Download PDF" button.
