---
date: 2026-10-13
clusters: [10, 6]
tests_added: 37
suite_tally: "test_10o_usage_recorder.py + test_6s_fleet_metrics_contract.py: 37 pass / 0 fail / 0 skip / 0 xfail (25 subtests, -v); full api_layer + audit_logging regression (-q): 271 pass / 0 fail / 1 xfail (pre-existing, unrelated to this batch)"
---

# Batches 10o and 6s — fleet metrics phase 1 (LEX-749), release R3

Fleet metrics phase 1, the lex-app half of release R3: [batch 10o](../../clusters/10-api_layer/batches.md)
records per-day presence by channel. The middleware writes API activity on `api`, and
`POST api/usage/area` writes the interface's area events on `ui` and `ui_embed`. With the
running version, that gives K1 and K2. [Batch 6s](../../clusters/06-audit_logging/batches.md)
pins the audit shape the Instance Controller's collector reads — columns, `timestamptz`
types, the API-key-name writer exclusion, and the K3 run-grouping contract, including a
create-triggered run (paired with 6r/7t) counting as exactly one finished run.

This session is the test-plan registration for both batches — the source and the test
files themselves landed in Tasks 1–6. Allocation changed once re-checked against the tree
actually on this branch: cluster 6's batch lands on letter **s**, not the original draft's
**r**, because `test_6r_create_triggered_run_audit.py` (6.119–6.120) already held that
letter; it carries 5 scenarios (6.121–6.125), not 4, to cover the create-triggered-run
case (6.125) the draft had omitted. Cluster 10's batch lands as planned on the next free
letter, **o**, 10.83–10.114.
