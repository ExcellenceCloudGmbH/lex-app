# Local agent rules — no-trigger testing parity

**Date:** 2026-05-28 (revised 2026-05-29)
**Status:** Implemented — testing scope. Other domains deferred.

## Problem

The Copilot Coding Agent (cloud) writes tests that follow the lex-app conventions (cluster naming,
base-class selection, coverage pairing) because the coverage workflow injects an explicit prompt
into the agent's issue. Local agents — IDE Copilot (Chat, inline) and Claude Code — have no such
prompt, so their suggestions drift from project conventions and fail review.

We want **local agents to produce work matching the cloud agent's quality**, with the test-plan
followed strictly. Two hard requirements shaped the design:

1. **No trigger words / no explicit invocation.** The agent must already "know" the rules. When a
   dev edits framework source, paired tests must be written automatically — not behind a
   `/command` the dev has to remember.
2. **Local mirrors cloud.** The behaviour of writing and running tests must follow the cloud
   coverage-gate paradigm: source change → paired test in the same change, cluster-allocated, with
   the plan kept in sync.

## What changed from the first MVP

The original MVP (PR #530) shipped an instructions file **plus** a `/write-cluster-test` slash
command. The slash command is a **trigger** — incompatible with requirement 1. It has been removed.
Its workflow logic was folded into:

- a **Claude Code skill** that auto-activates on description match (no `/` needed), and
- the **instructions file**, whose `applyTo` was widened to framework source so the rules load the
  moment a dev edits `lex/` code (not just when a test file is open).

## Architecture — layered, no-trigger

```
┌─ AGENTS.md (root) ──────────────────────────────────────────────┐
│  Cross-tool foundation. Read by Claude Code, Copilot coding      │
│  agent, Cursor, Codex. 3 prime directives + pointers. Lean.      │
└──────────────────────────────────────────────────────────────────┘
        │ points to
        ▼
┌─ .github/instructions/testing.instructions.md ───────────────────┐
│  Path-scoped (applyTo). Auto-injected by Copilot Chat + cloud     │
│  agent when an open/changed file matches the glob. The glob now   │
│  covers framework SOURCE (lex/lex_app, lex/core, lex/api, …) AND  │
│  all test files — so editing source pulls the testing rules in    │
│  with no trigger. Full prose rules + the §6 plan-update / bug     │
│  workflow.                                                        │
└──────────────────────────────────────────────────────────────────┘
        │ executable companion for Claude Code
        ▼
┌─ .claude/skills/lex-testing/SKILL.md ────────────────────────────┐
│  Auto-activates on description match (Claude Code / SKILL-aware   │
│  tools). 8-step workflow: read plan → identify cluster → allocate │
│  letter/scenario → type → confirm → scaffold → update plan →      │
│  run & report. Replaces the deleted slash-command prompt.         │
└──────────────────────────────────────────────────────────────────┘
```

`.github/copilot-instructions.md` is **left untouched** — it stays focused on MCP/kickstart
guidance for downstream Lex-app builders. Adding domain rules there would dilute its purpose, and
the path-scoped instructions file covers the testing case without it.

### Per-tool coverage

| Tool | Reads AGENTS.md | Reads `*.instructions.md` (applyTo) | Reads SKILL.md |
| --- | --- | --- | --- |
| Copilot coding agent (cloud) | yes | yes | no |
| Copilot Chat (VS Code / VS) | weak | yes (on glob match) | no |
| Copilot inline suggestions | no | no | no |
| Claude Code | yes | n/a | yes (auto-activate) |
| Cursor / Codex | yes | partial | no |

The CI coverage gate (`copilot_coverage_check.yml`) is the **enforcement backstop** for every tool —
including Copilot inline, which reads none of these files. If a local agent misses a paired test,
the gate opens a `coverage-task` issue and the cloud agent fills it. Local rules reduce how often
that happens; the gate guarantees it can never ship unpaired.

## The "keep the plan honest" behaviour (the minor adjustment)

The instructions already pointed at the test-plan for naming. This revision makes the **doc-update
and bug-recording** behaviour explicit, because the instructions for it already exist in the plan:

- **`test-writing-plan.md`** — every completed batch carries a Status / Tests-landed row (see 1o,
  1p, 7k). The agent appends/updates this row in the same change.
- **`known-bugs.md`** (lines 9-12) — documents the bug workflow: surface a real bug → assert the
  correct behaviour → `@unittest.expectedFailure` → add a `BUG-NNN` row. Never weaken the test.
- **`test-writing-plan.md` Rule 7** — reinforces: a test exposing a real bug gets
  `@unittest.expectedFailure` + a tracker entry, not a softened assertion.

These are now referenced from `testing.instructions.md` §6 and the skill's Step 7 so a local agent
performs them automatically.

## Files in this PR

1. **`AGENTS.md`** (new) — root cross-tool foundation.
2. **`.github/instructions/testing.instructions.md`** (revised) — `applyTo` widened to framework
   source; §6 plan-update/bug-recording added; runner updated to `python -m lex pytest`.
3. **`.claude/skills/lex-testing/SKILL.md`** (new) — auto-activating cluster-test workflow.
4. **`.github/prompts/write-cluster-test.prompt.md`** (deleted) — trigger-based, replaced by 2 + 3.
5. **This spec** (revised).

## Success criteria

- Editing framework source surfaces the testing rules with no trigger; the agent writes the paired
  test in the same change.
- Cluster-aligned tests get the right letter, scenario range, and update `test-writing-plan.md` in
  the same PR.
- Discovered bugs land in `known-bugs.md` with an `@expectedFailure` test, never a softened one.
- No new "should have read the test-plan" review comments on local-agent test PRs.

## Deferred (future work, in priority order)

1. **Other domain instruction files** — `calculation-models`, `audit-logging`, `migrations`,
   `frontend`. Same path-scoped pattern as testing.
2. **Auto-retargeting workflow** for coverage-task PRs that target the wrong base (defense-in-depth
   on PR #528's prompt hardening).
3. **CLI / MCP tool** returning canonical test-plan state (`next-slot --topic`) — build only if
   markdown-table parsing proves unreliable in practice.
4. **Org-level Copilot instructions** — lift cross-cutting rules to org settings if other repos
   need them.

## References

- [`AGENTS.md`](../../../AGENTS.md) — cross-tool foundation.
- [`.github/instructions/testing.instructions.md`](../../../.github/instructions/testing.instructions.md) — path-scoped testing rules.
- [`.claude/skills/lex-testing/SKILL.md`](../../../.claude/skills/lex-testing/SKILL.md) — Claude Code skill.
- [`.github/copilot-instructions.md`](../../../.github/copilot-instructions.md) — global Copilot file (untouched).
- [`lex/test_project/test-plan/`](../../../lex/test_project/test-plan/) — authoritative cluster + bug-tracker source.
- [`docs/superpowers/specs/2026-05-26-copilot-test-automation-pipeline-design.md`](2026-05-26-copilot-test-automation-pipeline-design.md) — coverage-task pipeline spec.
- PR #528 — coverage-task issue-body hardening (related, separate work).
