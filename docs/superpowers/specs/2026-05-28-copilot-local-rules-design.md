# Copilot local-rules — testing-first MVP

**Date:** 2026-05-28
**Status:** MVP — testing scope only. Future domain rules deferred.

## Problem

The Copilot Coding Agent (cloud) writes tests that follow the lex-app conventions (cluster naming, base-class selection, coverage pairing) because the coverage workflow injects an explicit prompt into the agent's issue. Local IDE Copilot (Chat, inline) has no such prompt — so its test suggestions drift away from project conventions and frequently fail review.

We want **local Copilot to produce work matching the cloud agent's quality**, with the test-plan followed strictly (no improvisation on cluster naming, letter allocation, or scenario IDs).

## Decisions

### Decision 1: Approach is **A + B** (instructions + prompt). C deferred.

Three options were considered:

- **A. Instructions file only.** Path-scoped instructions activate when a test file is open or the user asks Copilot about tests. Tells Copilot the rules and points at the test-plan. Cheap, but relies on Copilot remembering to read the live test-plan state.
- **B. Prompt that scripts the workflow.** `/write-cluster-test` slash command walks Copilot through the test-plan lookup and forces a confirmation step before scaffolding. Explicit and reliable, but only runs when the dev invokes it.
- **C. CLI / MCP tool returning canonical state.** `lex test-plan next-slot --topic <topic>` returns `{cluster, letter, scenario_range, path}` deterministically. Eliminates parse drift but requires building infrastructure.

**Chosen: A + B.** Instructions cover style/convention so any Copilot interaction in a test context inherits the rules. The prompt covers the workflow-heavy path (cluster allocation) where determinism matters most. **C is deferred** — building a CLI is a separate multi-hour task and the MVP needs to ship now.

### Decision 2: Scope is **testing only**

Other domain instructions (calculation models, audit logging, migrations, frontend) are valuable but separate work. One PR per domain keeps reviews bounded. Testing is the highest-leverage domain because:

- Every framework change needs paired tests (coverage gate).
- Test conventions are the most cited reason for review pushback.
- The cloud agent already writes tests in the target style — we have a working reference.

### Decision 3: Files shipped in this PR

1. **`.github/instructions/testing.instructions.md`** — scoped path-based instructions. `applyTo` covers `lex/tests/`, `lex/test_project/tests/`, and any `test_*.py` / `frontend/src/__test__/` files.
2. **`.github/prompts/write-cluster-test.prompt.md`** — explicit slash command for the test-plan workflow. Forces test-plan read → cluster identification → letter/scenario allocation → confirmation → scaffold → test-plan update.
3. **This spec doc** — captures decisions for future refinement.

### Decision 4: What's deferred (refinement later)

- **Other domain scoped instructions** — calculation-models, audit-logging, migrations, frontend. Worth doing; not blocking testing parity.
- **CLI / MCP tool** for canonical test-plan state. Build only if devs report repeat parse errors on the markdown tables.
- **Auto-retargeting workflow** for coverage-task PRs that target the wrong base branch. Tracked separately as defense-in-depth for the prompt-hardening fix in PR #528.
- **`.github/copilot-instructions.md` changes** — left untouched. It's currently focused on MCP/kickstart guidance for downstream Lex app builders; adding domain rules there would dilute its purpose.

## How the two files interact

```
Dev opens a test file in IDE
        ↓
testing.instructions.md auto-loads (applyTo glob match)
        ↓
Copilot Chat / inline suggestions have full LEX testing rules in context
        ↓
Dev asks "write a test for fast_health.py"
        ↓
   ┌────────────────────────┬─────────────────────────┐
   │                                                  │
Free-form chat                            Dev invokes /write-cluster-test
(general test, no cluster ↓               ↓ (cluster allocation matters)
allocation needed)            write-cluster-test.prompt.md runs
   ↓                                       ↓
Instructions guide the           Step-by-step lookup → confirm → scaffold
output (style, pairing,                    ↓
docstring, base-class)           Output: scaffolded file + test-plan row
```

## Success criteria

- IDE Copilot produces tests that pass the coverage gate's pairing check on first try.
- Cluster-aligned tests (when devs use `/write-cluster-test`) get the right letter, the right scenario range, and update the test-plan in the same PR.
- No new "should have read the test-plan" review comments on Copilot-assisted test PRs.

## Risks and mitigations

| Risk | Mitigation |
| --- | --- |
| Copilot mis-parses the test-plan markdown tables and picks a wrong letter. | The prompt forces an explicit confirmation step (Step 5) before scaffolding. Dev sees the proposed allocation and can correct it. |
| Devs forget to invoke `/write-cluster-test` and write free-form. | Acceptable for non-cluster-aligned tests under `lex/tests/unit/`. Cluster work is the minority. If this becomes a problem, escalate to a CI check that validates batch numbering on PR open. |
| `applyTo` glob misses some test file naming conventions. | Globs are broad on purpose: `lex/tests/**`, `lex/test_project/tests/**`, `**/test_*.py`, `**/tests/**/*.py`, `frontend/src/__test__/**`. Expand if a gap is reported. |
| Test-plan format changes break the prompt's lookup steps. | The prompt cites the canonical files; if the format changes, update the prompt. Coupling is acceptable for a project-specific tool. |

## Future work (in priority order)

1. **Domain instruction files** — `calculation-models.instructions.md`, `audit-logging.instructions.md`, `migrations.instructions.md`, `frontend.instructions.md`. Each follows the same pattern as testing.
2. **Auto-retargeting workflow** for coverage-task PRs (see follow-up note in PR #528's discussion).
3. **CLI / MCP tool** for test-plan state lookup — only if step 1 of the prompt (markdown table parse) proves unreliable in practice.
4. **Org-level Copilot instructions** — if other repos in the org need shared rules, lift the cross-cutting ones to the org settings UI.

## References

- [`.github/copilot-instructions.md`](../../../.github/copilot-instructions.md) — global Copilot file (untouched).
- [`.github/instructions/lex-docs.instructions.md`](../../../.github/instructions/lex-docs.instructions.md) — existing scoped instructions (template followed for new file).
- [`docs/superpowers/specs/2026-05-26-copilot-test-automation-pipeline-design.md`](2026-05-26-copilot-test-automation-pipeline-design.md) — coverage-task pipeline spec.
- PR #528 — coverage-task issue-body hardening (related but separate work).
- `lex/test_project/test-plan/` — authoritative source for cluster allocation rules.
