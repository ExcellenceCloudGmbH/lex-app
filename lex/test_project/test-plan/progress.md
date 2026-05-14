# Progress & Organization

> **Back to:** [Test Plan Index](index.md)
> **Decoupled May 2026** — see the per-file links below.

This page is now an index. The actual content is split across three files in [`progress/`](progress/):

| File | Owns | Update cadence |
|------|------|----------------|
| [`progress/conventions.md`](progress/conventions.md) | Methodology, naming, run-instructions, Quality Gates | Stable — edit only when a rule changes |
| [`progress/dashboard.md`](progress/dashboard.md) | Per-cluster status table, KPIs, Known Bugs Tracker | Every session — touch one row + (sometimes) one bug row |
| [`progress/session-log.md`](progress/session-log.md) | Chronological per-session narrative | Append-only — one row per session, never re-order |

## Why split?

The dashboard table and the Known Bugs Tracker get touched on almost every session; the conventions and the session log don't. Keeping them in one ~290-line file made every session change a merge-conflict candidate, and made the Copilot test-bot's "append one row" discipline impossible to enforce mechanically. The split mirrors the volatility, so each PR touches the smallest file.

## For the Copilot test-bot

The PR-gate workflow (`copilot_pr_gate.yml`) checks that test-bot PRs:

- modify exactly one row in [`progress/dashboard.md`](progress/dashboard.md) (the row for the touched cluster), or none if the touched cluster is brand-new (in which case a new row is appended at the bottom);
- append exactly one row to [`progress/session-log.md`](progress/session-log.md), at the bottom;
- never modify [`progress/conventions.md`](progress/conventions.md) — methodology changes are human-driven.

See [`docs/ci-cd/copilot-test-bot.md`](../../../docs/ci-cd/copilot-test-bot.md) for the full mechanism.

---

> **Back to:** [Test Plan Index](index.md) | **See also:** [Test Clusters](test-clusters.md) | [Expected Results](expected-results.md)
