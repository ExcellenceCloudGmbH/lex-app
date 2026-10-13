---
date: 2026-10-13
clusters: [10, 6]
tests_added: 37
suite_tally: "test_10o_usage_recorder.py + test_6s_fleet_metrics_contract.py: 37 pass / 0 fail / 0 skip / 0 xfail (25 subtests, -v); full api_layer + audit_logging regression (-q): 271 pass / 0 fail / 1 xfail (pre-existing, unrelated to this batch)"
---

# Batches 10o and 6s — fleet metrics phase 1 (LEX-749), release R3

Cluster 10's batch is letter o, scenarios 10.83–10.114 (32 tests):
[batch 10o](../../clusters/10-api_layer/batches.md) records per-day presence by channel —
the middleware writes API activity on `api`, and `POST api/usage/area` writes the
interface's area events on `ui` and `ui_embed`. With the running version, that gives K1
and K2.

Cluster 6's batch is letter s, scenarios 6.121–6.126 (6 tests):
[batch 6s](../../clusters/06-audit_logging/batches.md) pins the audit shape the
lex-analytics collector reads — columns, `timestamptz` types, the readable API-key name
column, and the K3 run-grouping contract, including a create-triggered run (paired
with 6r/7t) counting as exactly one finished run. 6.126 records that an API key's writes
carry `AnonymousUser` as their author, not the key's name (BUG-034, xfail strict).
