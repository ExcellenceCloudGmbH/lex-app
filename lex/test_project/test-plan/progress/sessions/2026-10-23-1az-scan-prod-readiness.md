---
date: 2026-10-23
clusters: [1]
tests_added: "11 new scenarios (1.444-1.454)"
suite_tally: "init, 1az: 35 pass / 0 fail"
---

# Batch 1az — the scan made ready for production databases

[1az](../../clusters/01-init/batches.md) reads each table once, ends its read-only transaction
after each table when run on its own, reports an earlier correction's backup table instead of
judging it, and dates C values and R11 suspects against the first frontend build that sent
explicit instants (2026-09-04 15:37 UTC). Each rule removed fails a named scenario. A 9999-12-31 sentinel no longer overflows: every timestamp is now read
as UTC digits (1.448). Large reports now travel gzipped and without D rows nobody acts on (1.449). A time with seconds but no microseconds is now told apart as made by code (1.450). Without any exposure, a value with no write time is placed outside it (1.451). A day's start or end stored two hours early is never passed as correct outside the periods (1.452, 1.453), and a column that stores midnight UTC in any version holds no B (1.454).
