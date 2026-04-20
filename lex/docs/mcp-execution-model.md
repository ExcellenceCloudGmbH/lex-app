# Lex MCP Local — Workflow Documentation

## Coordinator-Agent Architecture

The Lex MCP workflow uses a **coordinator-agent** pattern:

- **Coordinator** (IDE LLM — e.g. GitHub Copilot): A lightweight flow-control layer. It calls `get_plan_step`, invokes the step agent, calls `notify_step_complete`, and moves to the next step. It does NOT do step work itself.
- **Step agents** (`lex-step-00` through `lex-step-14`): Specialized agents that execute the actual work for each step. Each knows exactly what its step requires and reads the `./docs/` folder for framework rules.
- **Why**: This keeps the coordinator's context window lean while giving each step thorough, focused execution.

## Mandatory Workflow Pattern

**Every workflow execution MUST follow this pattern — no exceptions:**

**Agent discoverability rule:** if a user says "start", "begin", "initialize", "setup", "bootstrap", "kick off", "first step", or even misspells it as "kicstart", the FIRST tool call should be `kickstart_workflow` (new project) or `kickstart_run` (existing project).

```
kickstart_workflow (new project) OR kickstart_run (existing project)
    ↓
[delegate each step (0–14) to its lex-step-NN agent in order]
    ↓
finalize_workflow (MANDATORY — always call this at the end)
```

### When to use which kickstart:
- **`kickstart_workflow`** — ONLY for brand-new projects. Creates the GitHub repo, runs git init, pushes initial commit to main, creates workflow branch, and tracking issue. **Never call this on an existing project — it will destroy the git history.**
- **`kickstart_run`** — For existing projects that already have a GitHub repo. Creates a new workflow branch (`{repo}/run-{NN}`) and tracking issue. Does NOT create a repo or run git init. Use this every time you start a new session of work on an existing project.

### Fast routing rule (for tool selection)
- New repo / new project intent → `kickstart_workflow`
- Existing repo / continue / resume intent → `kickstart_run`
- Any non-kickstart workflow tool before those two is out of order

### What happens in a workflow run:
1. **Start** — `kickstart_workflow` or `kickstart_run` creates a workflow branch from `main`
2. **Work** — The coordinator calls `get_plan_step` for each step (0–14), delegates to the corresponding `lex-step-NN` agent, then calls `notify_step_complete` when the agent finishes. This is a strict sequential loop. The coordinator manages flow; agents do the work.
3. **End** — `finalize_workflow` commits any remaining changes, creates a PR from the workflow branch to `main`, squash-merges it, checks out `main`, and closes the tracking issue.

### Key rules:
- Steps (0–14) are MANDATORY. The coordinator MUST delegate each step to its agent in ascending order.
- The coordinator never does step work itself — it only manages the delegation loop.
- All steps are served by `get_plan_step` (unified index 0–14). There is no separate implementation step tool.
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

## Error Handling — Zero Tolerance

**This is the most important section. If errors are not handled this way, the product should not be used.**

### Philosophy

The system classifies every error into exactly two categories:

1. **Auto-fixable** — Trivial LLM mistakes (e.g. commit message too long → truncated silently). These are fixed by the backend without bothering the user. The fix must be deterministic and cannot itself fail.

2. **User-actionable** — Everything else. The workflow HALTS and the user is informed with clear troubleshooting steps. The LLM does NOT attempt to diagnose, fix, or work around the error.

**Unknown errors default to user-actionable (fail-safe).** If there's any doubt, the workflow stops.

### What happens on error

When any tool returns `ok: false`:
- The response includes `halt_and_notify_user: true`
- A `troubleshooting` object contains: what happened, error detail, steps to resolve, and what to do after
- An `llm_instruction` field explicitly tells the LLM to stop all work

### Rules for the LLM (enforced in server instructions)

1. **STOP ALL WORK IMMEDIATELY** — do not call any more tools
2. **Do NOT attempt to fix the error** — no git commands, no shell commands, no code changes
3. **Present the troubleshooting info to the user** clearly and completely
4. **WAIT for the user** to confirm the issue is resolved
5. **Then retry the same tool** that failed

These rules apply to ALL errors — git errors, GitHub API errors, network errors, missing files, corrupted state, or anything else. There are ZERO exceptions. The LLM is NEVER allowed to try to fix infrastructure problems itself.

### Auto-fixed errors (silent, no user interruption)

| Pattern | Fix |
|---|---|
| Commit summary too long (>250 chars) | Truncated with "..." |
| Commit summary contains newlines | Flattened to single line |
| Empty commit summary | Defaulted to "step work" |
| Nothing to commit | Treated as success, workflow continues |
| Repo name already exists (422) | Auto-incremented with `-2`, `-3`, etc. |

### User-actionable errors (workflow halts)

| Pattern | Troubleshooting |
|---|---|
| Auth failures (401/403) | Check GITHUB_TOKEN, verify scope, restart server |
| Rate limiting (429) | Wait and retry |
| Network/timeout errors | Check internet, proxy, firewall |
| Repo not found (404) | Verify name, check token access |
| Org permission denied | Check org permissions, try personal account |
| Missing project directory | Provide correct path or set LEX_MCP_PROJECT_DIR |
| Missing GitHub token | Create PAT, add to .env |
| Git merge conflicts | Resolve manually, then retry |
| Missing/corrupted .git folder | Re-clone, run git init, or git fsck |
| GitHub 422 (validation) | Check for duplicates, invalid input |
| GitHub 5xx (server error) | Wait and retry, check githubstatus.com |
| System command failures | Verify git installed, project dir accessible |
| No active project | Call kickstart_workflow or kickstart_run first |
| Any unknown error | Review details, check server logs, restart |

## Tool Summary

| Tool | Purpose | When |
|---|---|---|
| `kickstart_workflow` | Create new repo + workflow branch | First-ever run on a new project |
| `kickstart_run` | Create workflow branch on existing repo | Every subsequent run |
| `get_plan_step` | Load step instructions (0–14, unified) | During work |
| `get_deployment_step` | Load deployment step (optional) | During deployment |
| `notify_step_complete` | Commit + push step work | After completing a piece of work |
| `finalize_workflow` | Merge branch to main, close tracking | ALWAYS at the end |
| `resume_workflow` | Reconstruct context on new session | When picking up from another session |
| `inspect_github_repository` | Diagnostic — check repo state | Troubleshooting |
| `get_workflow_status` | Lightweight status check | Orientation / progress check |