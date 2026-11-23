---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "1cw + 1cz: 7 pass / 0 fail"
---

# lex.ui tables: a closed table keeps none of its rows (1cz)

Moving the reviewed customer project's tables to lex.ui showed that a table which left the screen
kept its last page - names and e-mail addresses among them - in the tab's state. `close` now
drops the page, the count, the totals and the filter choices. Batch
[1cz](../../clusters/01-init/batches.md); 1.815's hidden table holds no row either.
