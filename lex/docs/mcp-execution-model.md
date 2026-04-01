---
tags: [mcp, execution, workflow, lex]
---

# MCP Execution Model (Single Prompt)

This document defines the intended runtime behavior for LLM-driven project delivery in this repository.

## Goal

Run planning and implementation as one continuous workflow in a single prompt execution.

- No phase selection prompts.
- No approval-gate pauses between planning and implementation steps.
- Deployment is not part of this default flow.

## Canonical sequence

1. Initialize workflow context through MCP.
2. Start from the first planning step.
3. For each step in order:
   - Load step instructions from MCP.
   - Execute the step work.
   - Persist artifacts under `plans/<run-id>/...`.
   - Notify MCP that the step is complete.
4. Continue immediately to the next step.
5. After final planning step, continue directly into implementation steps.
6. End when all planning and implementation steps are completed.

## Runtime rules

- Treat `docs/` as read-only handbook content.
- Never write generated outputs into `docs/`.
- Never skip or reorder steps.
- Do not wait for explicit human approvals unless the user introduces a hard blocker or missing mandatory input.
- If a step is blocked by missing required business data, ask only for that missing data, then continue from the same step.

## Output discipline

- Keep each step output deterministic and tied to the current step objective.
- Maintain traceability across requirements, stories, models, and implementation artifacts.
- Record completion progression in `plans/<run-id>/run.md` as steps are finished.

## Mandatory Workflow Pattern

**Every workflow execution MUST follow this pattern — no exceptions:**

```
kickstart_workflow (new project) OR kickstart_run (existing project)
    ↓
[do work — steps are optional and flexible]
    ↓
finalize_workflow (MANDATORY — always call this at the end)
```

### When to use which kickstart:
- **`kickstart_workflow`** — ONLY for brand-new projects. Creates the GitHub repo, runs git init, pushes initial commit to main, creates workflow branch, tracking issue, and Jira epic. **Never call this on an existing project — it will destroy the git history.**
- **`kickstart_run`** — For existing projects that already have a GitHub repo. Creates a new workflow branch (`{repo}/run-{NN}`), tracking issue, and Jira task. Does NOT create a repo or run git init. Use this every time you start a new session of work on an existing project.

### What happens in a workflow run:
1. **Start** — `kickstart_workflow` or `kickstart_run` creates a workflow branch from `main`
2. **Work** — You freely call `get_plan_step`, `get_deployment_step`, `notify_step_complete` in any order. Steps are guidelines, not mandatory gates. You can skip steps, reorder them, or not use them at all.
3. **End** — `finalize_workflow` commits any remaining changes, creates a PR from the workflow branch to `main`, squash-merges it, checks out `main`, and closes the tracking issue.

### Key rules:
- Steps (0–12) are **optional guidance**, not mandatory gates. The AI is free to request any steps, skip steps, or do work without step guidance entirely.
- `notify_step_complete` is for committing and pushing work. Each call commits with `[step-NN/process] summary` and pushes to the workflow branch.
- `finalize_workflow` will always commit+push remaining uncommitted changes before creating the PR. This means even if you never called `notify_step_complete`, your changes will still be captured.
- If the AI needs to resume work from a different device/session, use `resume_workflow` to reconstruct context, then continue with `kickstart_run` for the next run.

## Change Detection System

When the user modifies files between step calls, the system detects this automatically:

1. At each `get_plan_step` call, `git status --porcelain` checks for uncommitted changes
2. Modified files are cross-referenced against the step-to-file manifest (`.lex-workflow/manifest.json`)
3. If files from a previous step were modified, the LLM is alerted and directed to re-execute from the affected step
4. New `.csv`/`.xlsx`/`.xls` files trigger re-execution from step 2 (IO step)
5. Other new files are noted but don't trigger re-execution

## Configuration

- `local_project_path` — pass to `kickstart_workflow` or `kickstart_run`, or set `LEX_MCP_PROJECT_DIR`
- Workflow remote defaults to `lex-origin` (configurable via `LEX_MCP_WORKFLOW_REMOTE`)
- Workflow branches follow the pattern `{repo_name}/run-{NN}` with auto-increment

## Tool Summary

| Tool | Purpose | When |
|---|---|---|
| `kickstart_workflow` | Create new repo + workflow branch | First-ever run on a new project |
| `kickstart_run` | Create workflow branch on existing repo | Every subsequent run |
| `get_plan_step` | Load step instructions (optional) | During work |
| `get_deployment_step` | Load deployment step (optional) | During deployment |
| `notify_step_complete` | Commit + push step work | After completing a piece of work |
| `finalize_workflow` | Merge branch to main, close tracking | ALWAYS at the end |
| `resume_workflow` | Reconstruct context on new session | When picking up from another session |
| `inspect_github_repository` | Diagnostic — check repo state | Troubleshooting |
| `update_jira_issue` | Update Jira issue | Manual Jira updates |

## Out-of-scope by default

- Deployment execution steps
- Additional review workflows unrelated to current step completion
- New process branches that break sequential step order
