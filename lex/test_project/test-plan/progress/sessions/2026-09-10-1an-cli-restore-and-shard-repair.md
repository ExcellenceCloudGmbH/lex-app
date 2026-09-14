---
date: 2026-09-10
clusters: [1]
tests_added: 0
suite_tally: "init: 534 pass / 0 fail / 13 skip / 134 subtests (was 6 fail); 1ae: 41 pass / 0 fail (was 6 fail / 35 pass); 1ad: 56 collected, 56 defined (was 66 defined / 56 collected); whole suite: 1559 pass / 32 fail, none in cluster 1 after the 1.281/1.282 fix"
---

# Merge `3d14e959` deleted production code that the tests on disk import

No new scenarios. This session repairs what one bad merge resolution did to
[cluster 1](../../clusters/01-init/batches.md), which the 10 September platform
health report surfaced as a broken capability: *Project initialisation — 6 failed*.

## What the report actually showed

All six failures were the same shape, and none of them were assertion failures:

```
ImportError: cannot import name '_safe_theme_flags' from 'lex.bin.lex'
ImportError: cannot import name '_resolve_streamlit_ports' from 'lex.bin.lex'
```

`test_1ae_streamlit_theme.py` was on the branch. The `lex/bin/lex.py` code it
imports was not. Both helpers landed on 30 July (`89e6e1cc`, `441f4842`) and were
still present in v2.2.0 (`85479920`), integrated with the proxy work by hand —
the comment on the port resolution says so explicitly. Merge `3d14e959`
(2026-09-09, local `lex-app-v2` ← `origin/lex-app-v2`) resolved `lex/bin/lex.py`
in favour of *ours* and dropped all three hunks: the theme flags, the port
resolver, and the Streamlit version-floor diagnostic. That merge is mine, and it
is on `origin/lex-app-v2`, which is why CI on `v2.2.1` was red.

`lex/bin/lex.py` is restored from `85479920` verbatim. That version is a strict
superset: every symbol this branch added (`_warn_if_sessions_are_not_durable`,
`LEX_STREAMLIT_DISCONNECTED_SESSION_TTL`, `uvicorn.Server`,
`LEX_INTERNAL_AUTH_SECRET`, `LEX_PROXY_SHUTDOWN_TIMEOUT`) is in it, plus the
three that were lost. Nothing was re-derived by hand.

## The same merge silently un-ran ten tests

`test_1ad_proxy_assets_and_session_durability.py` came out of that merge with the
class block for 1.277–1.286 present **twice**. Two classes, one name: Python keeps
the second, so ten methods were defined and never collected — 66 defined, 56
collected, and no error anywhere. The version in v2.2.0 was the fixed one and also
carried ten tests this branch had never seen (`StaleConnectionRecovery` 1.293–1.295,
`AccessLogging` 1.296–1.299, `RefresherSurvivesReruns` 1.300–1.302). Restored from
`85479920`: 56 defined, 56 collected, and `defined == collected` is now checked
rather than assumed.

## And it truncated the plan shards, which is where the collisions came from

`allocation.yaml` went from 977 lines to 357 and `max_scenario: 312` to `292`;
`batches.md` from 1379 lines to 333. The records for letters `ae`–`aj` were
deleted outright, and the `ac` record was left glued into the middle of `aj`'s
mapping — a second `title:`/`scenarios:`/`note:` in one mapping, where YAML keeps
only the last, so `aj`'s own record was being discarded on load. `ad` appeared
twice as a key for the same reason.

That truncation is the direct cause of the allocation collisions flagged earlier:
with the shard claiming 292, three batches picked ranges that were already taken.
The letters map is rebuilt from `85479920`, `aj` has its own record back, `ak`/`al`/`am`
(which landed with no entry at all) are recorded from measured counts, and the two
double-allocated letters are recorded as such — **not** renumbered, per the skill's
rule that letters never are. Their scenario overlap is still real and still needs
the two owners; `test_plan_aggregates.py validate` names every case.

## 1an moved off its own collision

Batch 1an took 1.300–1.305 because the shard said the ceiling was 292. On disk
that range belongs to `1af`/`1ag`/`1ah`. It is now **1.336–1.341**, above
everything on disk, and `max_scenario` says 341. No test logic changed — the
HTTP/2 header work itself is unchanged and still 6 pass / 0 fail, with 1.341
failing only when the buffered branch's header drop is removed.

## What the whole-suite run then found in cluster 1

Two of the ten restored self-review scenarios were themselves wrong, and only a
whole-suite run could show it:

```
AssertionError: 4804734144 == 4804734144 : a client from a finished loop must not be handed to the next one
```

The first read of that — CPython reusing a freed object's address — was wrong,
and comparing live objects with `assertIsNot` did not fix it. Instrumented and
measured instead:

```
asyncio.run from: nest_asyncio
loop ids: 4838791248 4838791248   same object: True
client ids: 4860205136 4860205136 same object: True
```

Something ahead of this cluster in a whole-suite run imports a module that calls
`nest_asyncio.apply()`, which replaces `asyncio.run` with a version that reuses
the **current** loop. Both calls then share one loop, so the proxy correctly
returns the same client and the same lock — the assertion was failing against
*correct* behaviour, and the test's premise ("two successive event loops") was
simply not true in that context. Nothing in the init cluster alone applies the
patch, which is why it only ever failed in the whole suite.

Both scenarios now build their loops with `asyncio.new_event_loop()` +
`run_until_complete`, which no patch of `asyncio.run` can affect, assert that
the two loops really are distinct before asserting anything about the objects,
and hold both loops live — `_LOOP_LOCKS` is keyed by `id(loop)` and keeps no
reference, so a collected loop's address could be inherited by a later one
(recorded as BUG-033). Mutation-tested: dropping either per-loop guard in
`lex/proxy.py` (`_UPSTREAM_CLIENT_LOOP is loop`, `_loop_lock`'s key) fails
exactly the matching scenario and nothing else. In the context that reproduced
the failure (calculation_logging + celery_async ahead of the class) cluster 1
goes from 2 failures to 0.

Whole suite afterwards: 1559 pass / 32 fail, and **none of the 32 is in cluster
1**. BUG-030 is updated with what those 32 actually are — 23 order-dependent
calculation_logging failures (0 alone, 9 per-cluster, 23 whole-suite), 6 in the
opt-in `stress` group, and `gate_selftest`'s one deliberate failure.
