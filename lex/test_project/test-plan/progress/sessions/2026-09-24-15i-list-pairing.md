---
date: 2026-09-24
clusters: [15i]
tests_added: "0 new scenarios; coverage-pairing follow-up for 15i"
suite_tally: "15i 9 pass / 0 fail / 1 skip locally on DATABASE_DEPLOYMENT_TARGET=local; coverage detector now matches List.py to the existing grid regression file"
---

**Batch 15i follow-up** — the existing regression file already covered the
`List.py` calculation-row list path, but the coverage-task heuristic only pairs a
source file when the changed test imports that module path or names its filename
stem. This follow-up makes that coverage explicit in
[batch 15i](../../clusters/15-calculation_logging/batches.md) without changing
the scenario scope.
