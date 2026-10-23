---
date: 2026-10-23
clusters: [1]
tests_added: "8 new scenarios (1.436-1.443), 1.435 extended"
suite_tally: "init, 1az: 24 pass / 0 fail"
---

# Batch 1az — the scan's audit-log rules

[1az](../../clusters/01-init/batches.md) gains the rules for dating exposure that the lex source
calls for: audit rows that do not hold the moment of the change (creates, deletes, data loads,
Calculate rows rewritten without microseconds) no longer decide an era, a change between a
pre-incident and a fixed version without affected rows is uncertain, and the lex-app version
running at scan time counts as a reading. Silent stretches and pre-rc212 anomalies are
reported, and a stated `--window` now adds to the audit log's periods. Each rule removed fails
a named scenario; a run on a copy of an rc221 instance is recorded in the batch.
