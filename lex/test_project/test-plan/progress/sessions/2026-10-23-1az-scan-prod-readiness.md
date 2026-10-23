---
date: 2026-10-23
clusters: [1]
tests_added: "4 new scenarios (1.444-1.447)"
suite_tally: "init, 1az: 28 pass / 0 fail"
---

# Batch 1az — the scan made ready for production databases

[1az](../../clusters/01-init/batches.md) reads each table once, ends its read-only transaction
after each table when run on its own, reports an earlier correction's backup table instead of
judging it, and dates C values and R11 suspects against the first frontend build that sent
explicit instants (2026-09-04 15:37 UTC). Each rule removed fails a named scenario.
