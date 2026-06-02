# Progress & Organization

> **Back to:** [Test Plan Index](index.md)
> **Decoupled May 2026** — see the per-file links below.

This page is now an index. The actual content is split across three files in [`progress/`](progress/):

| File | Owns | Update cadence |
|------|------|----------------|
| [`progress/conventions.md`](progress/conventions.md) | Methodology, naming, run-instructions, Definition of Done, Quality Gates | Stable — edit only when a rule changes |
| [`progress/dashboard.md`](progress/dashboard.md) | Per-cluster status table + KPIs | Every session — touch the row for the touched cluster |
| [`progress/session-log.md`](progress/session-log.md) | Chronological per-session narrative | Append-only — one row per session, never re-order |

Bug rows (BUG-NNN) live in **[`known-bugs.md`](known-bugs.md)** — the gate-enforced source of truth — not in the dashboard.

## Why split?

The dashboard table gets touched on almost every session; the conventions and the session log don't. Keeping them in one ~290-line file made every session change a merge-conflict candidate, and made the Copilot test-bot's "append one row" discipline impossible to enforce mechanically. The split mirrors the volatility, so each PR touches the smallest file.

## For the Copilot test-bot

The PR-gate workflow (`copilot_pr_gate.yml`, via [`.github/scripts/copilot_validate_pr_shape.py`](../../../.github/scripts/copilot_validate_pr_shape.py)) enforces that a test-bot PR:

- adds a new test file under `lex/test_project/tests/<cluster>/` named `test_<Nx>_<slug>.py`;
- modifies [`test-clusters.md`](test-clusters.md) (the canonical scenario list);
- appends a row to [`progress/session-log.md`](progress/session-log.md) (at the bottom, never re-ordered);
- in **bug-repro** / **fix-and-test** modes, adds a `BUG-NNN` row to [`known-bugs.md`](known-bugs.md);
- keeps source changes within the per-mode limits (regression and bug-repro are test-only; fix-and-test caps the source diff at 50 lines and lists every source file under a `### Source changes` body heading);
- links the originating issue with `Fixes #N` in the PR body.

Touching the cluster's row in [`progress/dashboard.md`](progress/dashboard.md) and never hand-editing [`progress/conventions.md`](progress/conventions.md) are conventions the bot follows, but the gate does **not** mechanically check them.

See [`docs/ci-cd/copilot-test-bot.md`](../../../docs/ci-cd/copilot-test-bot.md) for the full mechanism.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Test Clusters](test-clusters.md) | [Expected Results](expected-results.md)
