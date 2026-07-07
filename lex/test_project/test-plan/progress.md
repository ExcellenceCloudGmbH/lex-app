# Progress & Organization

> **Back to:** [Test Plan Index](index.md)
> **Decoupled May 2026** — see the per-file links below.

This page is now an index. The actual content is split across `progress/`:

| File | Owns | Update cadence |
|------|------|----------------|
| [`progress/conventions.md`](progress/conventions.md) | Methodology, naming, run-instructions, Definition of Done, Quality Gates, surface rule | Stable — edit only when a rule changes |
| [`progress/dashboard.md`](progress/dashboard.md) | Per-cluster status table + KPIs | ⚙ **Generated** — never hand-edit; rebuild with `python .github/scripts/test_plan_aggregates.py build` |
| [`progress/sessions/`](progress/sessions/) | Chronological per-session narrative — one file per session | Add a NEW `YYYY-MM-DD-<slug>.md` per PR; never re-order or edit prior fragments |

Per-cluster allocation, scenarios, and batch history are sharded under [`clusters/NN-<slug>/`](clusters/) (`cluster.md` / `batches.md` / `allocation.yaml`). Bug rows (BUG-NNN) live in **[`known-bugs.md`](known-bugs.md)** — the gate-enforced source of truth — not in the dashboard.

## Why sharded?

Per-cluster status, allocation, and session narrative all used to churn in a few shared files, making every session change a merge-conflict candidate and the "append one row" discipline impossible to enforce mechanically. Sharding per cluster (`clusters/NN-<slug>/`) plus one-file-per-session fragments (`progress/sessions/`) means each PR touches its own files and never collides; the dashboard is regenerated, not hand-edited.

## For the Copilot test-bot

The PR-gate workflow (`copilot_pr_gate.yml`, via [`.github/scripts/copilot_validate_pr_shape.py`](../../../.github/scripts/copilot_validate_pr_shape.py)) enforces that a test-bot PR:

- adds a new test file under `lex/test_project/tests/<cluster>/` named `test_<Nx>_<slug>.py`;
- for **each** touched cluster, edits `clusters/NN-<slug>/allocation.yaml` (letter entry + `max_scenario`) and `batches.md` (batch block), plus `cluster.md` when new scenarios are defined;
- adds ONE new session fragment under [`progress/sessions/`](progress/sessions/) (`YYYY-MM-DD-<slug>.md`, front-matter + short prose linking the batch);
- regenerates the dashboard (`python .github/scripts/test_plan_aggregates.py build`) and commits `progress/dashboard.md`; the retired monolith stubs stay frozen;
- in **bug-repro** / **fix-and-test** modes, adds a `BUG-NNN` row to [`known-bugs.md`](known-bugs.md);
- keeps source changes within the per-mode limits (regression and bug-repro are test-only; fix-and-test caps the source diff at 50 lines and lists every source file under a `### Source changes` body heading);
- links the originating issue with `Fixes #N` in the PR body.

Two dedicated gate steps back this — **Test-plan dashboard freshness** (the committed dashboard must match a fresh `build`) and **Test-plan allocation consistency** (test files cross-checked against `allocation.yaml`). Never hand-editing [`progress/conventions.md`](progress/conventions.md) or `progress/dashboard.md` is a convention the bot follows.

See [`docs/ci-cd/copilot-test-bot.md`](../../../docs/ci-cd/copilot-test-bot.md) for the full mechanism.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Testing Philosophy](testing-philosophy.md) | [Clusters](clusters/) | [Expected Results](expected-results.md)
