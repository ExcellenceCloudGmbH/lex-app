---
date: 2026-07-29
clusters: [1]
tests_added: 6
suite_tally: "1z: 6 pass / 0 fail"
---

# Batch 1z — migration-only history skip

Deploying a new instance was OOM-killed applying migrations, at the highest memory
setting the operator believed was available. See
[batch 1z](../../clusters/01-init/batches.md).

The database is empty on a new instance, so the cost is not row data. It is model
classes: every tracked model gains a Level 1 `Historical<X>` and a Level 2
`Meta<Historical<X>>`, each with the parent's full field set, and
`generic_app_config.ready()` builds all of them unconditionally — including in a
process whose only job is to apply migration files. The migration executor then
builds its own `ProjectState` over the same tripled set.

`scripts/benchmark_history_registration_memory.py` was written to test the claim
rather than assert it. It reports **exactly 3.0x model classes** and ~0.21 MiB per
model, linear from 50 to 400 models. Note it measures only the app-registry half —
the half this change removes — so it is a lower bound on the saving during
`migrate`.

The guard is a positive match on known-safe command lines and nothing else.
`makemigrations` must always see the history models: they are constructed at
runtime, and if they are absent while autodetection runs Django treats them as
deleted and writes migrations that drop the history tables. Scenario 1.212 exists
solely to hold that line.
