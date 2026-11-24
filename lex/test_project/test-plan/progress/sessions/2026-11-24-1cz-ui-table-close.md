---
date: 2026-11-24
clusters: [1]
tests_added: 1
suite_tally: "every Reflex batch (1ap 1aq 1bt-1bz 1ca-1cz 4n 10ag 10d): 175 pass / 0 fail, 237 subtests pass"
---

# lex.ui tables: a closed table keeps none of its rows (1cz)

Moving the reviewed customer project's tables to lex.ui showed that a table which left the screen
kept its last page - names and e-mail addresses among them - in the tab's state. `close` now
drops the page, the count, the totals and the filter choices. Batch
[1cz](../../clusters/01-init/batches.md); 1.815's hidden table holds no row either. 1cw's `build_people_table` now returns its own table's state
(`_CACHE_BY_TABLE`), not the last one built for the handler, so the batches pass in any order.
