---
date: 2026-11-06
clusters: [1, 8]
tests_added: "5 new scenarios (1.410-1.411, 8.154-8.156); 1.368 rewritten; BUG-046 resolved"
suite_tally: "single-process lex/test_project/tests: 1845 pass / 33 fail / 27 skip / 6 xfail / 416 subtests in 36 min, no hang. Every failure is pre-existing: calculation_logging 24 (the same set on v2.4.2); 1aq 1.357c and 1at 1.381 (2 subtests), both v2.4.2 baseline failures; gate_selftest (fails by design); stress 4 (11.14 fails run alone on v2.4.2 too; 11.2, 11.13 and 11.22 fail only under whole-suite load); 10a 10.10 (a missing dynamic table, once; it passes alone, in its cluster, and with whole-tree collection)"
---

# Batches 1ay and 8d: lex no longer applies nest_asyncio (BUG-046)

See [1ay](../../clusters/01-init/batches.md) and [8d](../../clusters/08-celery_async/batches.md).
In a process where `nest_asyncio.apply()` had run, the second `async_to_sync` in a thread could
wait forever on a loop that nobody ran. lex applied that patch in two places. `LexAppConfig.ready()`
applied it in every downstream process. The startup reset applied it too, and inside a running uvloop
loop the reset's patch stopped startup instead. The reset now runs its ORM work on a thread of its
own, and `ready()` applies nothing. Each new scenario starts a fresh interpreter with a watchdog, so a
regression fails the test instead of hanging the suite. Each one fails on v2.4.2.

The bug was found because a single-process run of `lex/test_project/tests` hung at 1aq 1.360: 8x's
tearDown had applied the patch for the rest of the run. That tearDown no longer applies it, and the
same run now completes in 36 minutes (1845 passed, 33 failed). Each of the 33 failures also
occurs without this change; `suite_tally` above lists them. 1.368 passed this time, after
failing on v2.4.2 because its child could not import lex.
