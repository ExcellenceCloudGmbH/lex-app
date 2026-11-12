---
date: 2026-11-10
clusters: [1]
tests_added: "10 new scenarios (1.609-1.618); 1.391 joins the load thread async_ready now returns"
suite_tally: "1bj + 1av + init/initial_data + 8j: 55 pass / 1 skip / 0 fail"
---

# Batch 1bj — the initial-data load survives restarts and a second server process

Production showed one process starting the initial data load and a second one, moments later,
reporting "not triggered" on 5 quarters, 8 funds and 89 of 179 companies; the seed was never
completed. The production start script runs `lex start --reload`, whose restart terminates the
server process and joins it. Since 7abf2a69 the load thread inherited `daemon=True` from the
`lex-async-ready` thread, so the exiting process no longer waited for it and the load died
mid-write; the "models are empty" gate then read the partial rows as existing data forever.

`lex/lex_app/initial_data_load.py` now runs the decision and the load in a non-daemon thread
under a PostgreSQL session advisory lock and records the outcome in
`lex_initial_data_load_state`. See [batch 1bj](../../clusters/01-init/batches.md).
