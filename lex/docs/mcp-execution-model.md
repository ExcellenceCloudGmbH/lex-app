# Lex MCP Local - Execution Model

This document describes how the current Lex MCP server executes work in a
downstream Lex project. It replaces the older forward-only 0-14 step model.

## Current Shape

`lex-mcp` is a unified FastMCP server. It imports six independent mode
surfaces and exposes exactly one surface to the IDE assistant at a time.

| Mode | Execution style | Main purpose |
| --- | --- | --- |
| `forward` | Coordinator-agent loop | Build a full new Lex App through steps 0-19 |
| `backward` | Coordinator-agent loop | Reverse-document an existing project |
| `edit` | Flat task-catalog run | Make targeted changes to an existing Lex app |
| `review` | Flat review run | Produce audit/review reports |
| `mvp_generator` | Checkpoint-gated flat run | Generate a constrained MVP |
| `mvp_completion` | Coordinator-agent loop | Expand an MVP into full-product scope |

The coordinator is the IDE LLM. The coordinator calls MCP tools, receives the
next brief, invokes the named downstream agent, and reports completion back to
the MCP. The coordinator must not do step or task work itself when the MCP has
returned a specific agent to run.

## Universal Execution Rules

These rules apply to every mode.

1. Select the correct mode before starting work. Use `docs/mcp-modes.md` for
   the quick mode picker.
2. The first mode-specific action is always the mode's kickstart tool:
   `kickstart_workflow`, `kickstart_run`, `reverse_kickstart`,
   `kickstart_edit`, `kickstart_review`, or `kickstart_mvp`.
3. When a tool returns a brief with an agent name, invoke that agent as the
   worker and keep the coordinator focused on orchestration.
4. When any MCP tool returns `ok: false`, stop immediately, show the
   troubleshooting payload to the user, wait for the user to resolve the
   issue, then retry the same tool.
5. Do not switch modes to bypass an error. Switch only when the user intent
   genuinely changes or a stale-tool payload tells you the requested tool
   belongs to another mode.

## Mode Routing

Use user intent first; tool names are secondary.

| User intent | Correct mode |
| --- | --- |
| Build a new Lex app from scratch | `forward` |
| Build only an MVP quickly | `mvp_generator` |
| Finish, expand, or complete an MVP to full product | `mvp_completion` |
| Modify or add a feature to an existing Lex app | `edit` |
| Review or audit an existing project | `review` |
| Document or reverse-document existing code | `backward` |

Priority rules:

1. "MVP" plus "complete", "finish", "expand", or "full product" means
   `mvp_completion`.
2. "MVP" plus initial creation means `mvp_generator`.
3. Existing-project implementation changes without MVP-completion wording mean
   `edit`.
4. Never use `edit` for MVP completion.
5. Ask exactly one clarification question when intent matches multiple rows.

## Unified Runner and Mode Switching

The recommended entry point is the unified `lex-mcp` process. Boot mode is
resolved in this order:

1. CLI `--mode`.
2. One-shot override file at `~/.lex-mcp/mode-override`.
3. Nearby `mcp.json` launch arguments.
4. Default `forward`.

`LEX_MCP_MODE` is passive reflection, not authoritative input. The server
rewrites it to match the actual running mode.

In unified mode, `switch_to_mode(target_mode=...)` delegates to
`mode_switch.live_switch_mode(...)`, which swaps the mounted FastMCP provider,
updates external state, bumps the tool-surface epoch, and sends
`notifications/tools/list_changed` so the IDE refreshes its tool list.

Every surface installs stale-tool middleware. If an IDE calls a tool from a
previous mode, the server returns a structured payload with
`stale_tool_call: true`, the active mode, an optional suggested mode, the
current `tool_surface_epoch`, and refresh/retry guidance.

## Forward Mode

Use `forward` for full new-product creation.

```text
/lex-app "..."                         # user prompt
kickstart_workflow(...)                # new GitHub repo and first run branch
  OR
kickstart_run(...)                     # new run branch on existing GitHub repo

for step N in 0..19:
  get_plan_step(step=N)
  runSubagent("lex-step-NN", brief)
  notify_step_complete(step=N, process, summary)

finalize_workflow()                    # stage 1: audit-report instructions
write plans/technical_docs/audit-report.md
finalize_workflow(audit_complete=True) # stage 2: PR, squash merge, issue close
```

Forward phases:

| Phase | Steps | Purpose |
| --- | --- | --- |
| Planning | 0-8 | Overview, IO, requirements, story, architecture, functions, diagrams, pseudocode, compliance |
| Implementation | 9-11 | Implementation plan, blueprint, code delivery |
| Hardening | 12-14 | Initial data, Streamlit/dashboard work, Lex compliance |
| Wiki | 15-18 | AST scaffold and technical-map enrichment |
| Sync | 19 | Forward/backward doc and code reconciliation |

`get_plan_step` detects user changes between steps. Tracked changes to files
owned by completed steps can force re-execution from the earliest affected
step. New `.csv`, `.xlsx`, or `.xls` files force re-execution from the IO
step. Other new untracked files are noted but do not force re-execution by
themselves.

Forward-style modes commit and push after each `notify_step_complete` call.
The coordinator should not manually run git commands during a managed workflow.

## Backward Mode

Use `backward` to reverse-document an existing project.

```text
/lex-reverse "<path or GitHub URL>"
reverse_kickstart(project_path=... OR github_url=...)
scan_project()
generate_wiki()

for step N in 3..6:
  get_reverse_step(step=N)
  runSubagent("lex-reverse-step-NN", brief)
  notify_reverse_complete(step=N, summary)

generate_questionnaire()
PAUSE while the user fills discovery-questionnaire.md
submit_questionnaire(path=...)

for step N in 8..16:
  get_reverse_step(step=N)
  runSubagent("lex-reverse-step-NN", brief)
  notify_reverse_complete(step=N, summary)

finalize_reverse()
runSubagent("lex-reverse-step-17", gap-report brief)
notify_reverse_complete(step=17, summary)
```

Backward mode may clone a GitHub URL for inspection, but it does not commit,
push, create branches, open PRs, or merge. Outputs land in the downstream
project's docs and technical-map folders.

## Edit Mode

Use `edit` for focused implementation changes to an existing Lex app.

```text
/lex-edit
kickstart_edit(project_path, change_description, context="")
list_edit_tasks(category?)              # optional
propose_edit_plan(change_description)   # optional helper
set_edit_plan(task_ids=[...], rationale="...")

for each task in the final plan:
  get_task_brief(task_id)
  runSubagent(brief.agent, brief)
  notify_task_complete(task_id, summary, artifacts=[...])
  OR waive_task(task_id, rationale)      # waivable sustainability tasks only

finalize_edit()
```

Edit mode is task-catalog driven, not stage driven. The coordinator chooses
the smallest useful set of atomic code-change tasks. The server auto-injects
sustainability tasks such as Lex compliance, dependent-code scan, tests,
docs sync, technical-map sync, and migration checks. `lex-compliance-check`
is non-waivable. `migration-check` is non-waivable when a model-shaped change
requires it.

Edit mode writes local code and edit artifacts. It does not commit, push,
branch, open PRs, or merge.

## Review Mode

Use `review` for static audits.

```text
/lex-review "<path or GitHub URL>"
kickstart_review(project_path=... OR github_url=...)
list_review_types()                     # optional

for each requested review type:
  get_review_brief(review_type="convention"|"business", ...)
  runSubagent("lex-review-...", brief)
  notify_review_complete(review_type, summary, artifacts, findings_count, severity)

finalize_review()
```

Review mode is intentionally flat. After `kickstart_review`, every tool is
optional and `finalize_review` is idempotent. Built-in review types are
`convention` and `business`. Reports land under `reviews/`; the MCP does not
commit them.

## MVP Generator Mode

Use `mvp_generator` for a constrained first version.

```text
/lex-mvp
kickstart_mvp(project_path, problem_statement, project_name?, resume=False)
list_mvp_checkpoints()                  # optional
submit_mvp_plan(plan={...})
submit_mvp_scope(entities=[...], requirements=[...], rationale="...")
scaffold_mvp()
runSubagent("lex-mvp-implementer", scaffold_manifest + scope)
record_implementation(files_written, notes="")
run_boot_checklist()
  if blocking:
    runSubagent("lex-mvp-verifier", checklist_result)
    runSubagent("lex-mvp-implementer", verifier_report)
    run_boot_checklist()
finalize_mvp()
```

Current MVP caps:

| Cap | Value |
| --- | --- |
| Requirements | 10 max, 240 characters each |
| Entities | 5 max |
| Fields per entity | 8 max |
| Demo walkthrough | At least 3 steps in `submit_mvp_plan` |
| Calculations | Dumb-but-real calculations; no numeric cap in the current code |

`scaffold_mvp` writes a deterministic skeleton including `Inputs/`,
`Uploads/`, `Reports/`, `demo/`, `tests/`, `model_structure.yaml`,
`lex_config.py`, `requirements.txt`, and `README.md`. `finalize_mvp` is gated
by `run_boot_checklist` and refuses to close while critical boot-contract
checks fail. MVP mode uses local git checkpoints when git is available, but it
does not push to a remote.

## MVP Completion Mode

Use `mvp_completion` when an MVP already exists and the user asks to complete,
finish, expand, or upgrade it to full-product scope.

```text
kickstart_run(...)                      # existing MVP project

for step N in 9..19:
  get_plan_step(step=N)
  runSubagent("lex-step-NN" or "lex-step-11-refactor", brief)
  notify_step_complete(step=N, process, summary)

finalize_workflow()
write plans/technical_docs/audit-report.md
finalize_workflow(audit_complete=True)
```

Completion mode assumes planning steps 0-8 already exist from the MVP path.
It uses the same forward-style Git/GitHub machinery and starts meaningful work
at step 9. Step 11 can route to `lex-step-11-refactor` when existing code must
be reconciled instead of created from scratch.

## Agents and Payload

The live downstream payload lives inside package-local `.github/` trees:

| Mode | Agent payload |
| --- | --- |
| `forward` | `src/lex_mcp_local/.github/agents/lex-step-00` through `lex-step-19`, plus support agents |
| `backward` | `src/lex_mcp_reverse/.github/agents/lex-reverse-step-00` through `lex-reverse-step-17` |
| `edit` | `src/lex_mcp_edit/.github/agents/lex-edit-*` |
| `review` | `src/lex_mcp_review/.github/agents/lex-review-*` |
| `mvp_generator` | `src/lex_mcp_mvp/.github/agents/lex-mvp-scoper`, `lex-mvp-implementer`, `lex-mvp-verifier` |
| `mvp_completion` | `src/lex_mcp_mvp_completion/.github/agents/`, steps 9-19 plus support agents |

Slash commands are also shipped from package-local `.github/prompts/` files:
`/lex-app`, `/lex-resume`, `/lex-reverse`, `/lex-reverse-resume`,
`/lex-edit`, `/lex-review`, and `/lex-mvp`. `mvp_completion` is entered by
mode switch rather than a dedicated slash command.

Repository-root `.github/` is not the downstream payload.

## Outputs in Downstream Projects

Depending on mode, Lex MCP writes these project-local artifacts:

| Path | Purpose |
| --- | --- |
| `.lex-workflow/manifest.json` | Forward/completion coordinator state |
| `plans/technical_docs/` | Forward planning docs and audit report |
| `plans/business_docs/` | Backward canonical business docs |
| `technical-map/` | AST/wiki module map and `CONTEXT.md` files |
| `reviews/` | Review reports |
| `edits/` | Edit task artifacts and `EDIT-REPORT.md` |
| `mvp/<run_id>/` | MVP plan, scope, checklist, and `MVP-REPORT.md` |
| `docs/` | Copied Lex framework topic references |
| `AGENTS.md` | Downstream project agent rules written by forward-style setup |

These outputs are created in the downstream project, not inside this
`lex-mcp-local` repository.

## Git and GitHub Ownership

| Mode | Local commits | Pushes | Branches | PRs / merge |
| --- | --- | --- | --- | --- |
| `forward` | Yes | Yes | Yes | Yes |
| `mvp_completion` | Yes | Yes | Yes | Yes |
| `mvp_generator` | Yes, local only | No | No remote branch | No |
| `backward` | No | No | No | No |
| `edit` | No | No | No | No |
| `review` | No | No | No | No |

Forward-style workflow branches use this shape:

```text
<repo-or-requested-name>/run-NN
```

`NN` is zero-padded and auto-incremented. `notify_step_complete` commits with:

```text
[step-NN/<process>] <summary>
```

Current caveat: `finalize_workflow(audit_complete=True)` attempts to squash
merge the PR after creating it. The code contains a production-mode helper
intended to disable auto-merge, but the finalize path does not currently call
that helper.

## Error Handling

The MCP server classifies errors as either deterministic auto-fixes or
user-actionable failures.

Auto-fixed examples include commit-summary cleanup, empty commit summaries,
no-op commits, and a few deterministic git repair cases.

User-actionable examples include missing credentials, permission failures,
network errors, missing paths, GitHub API failures, git conflicts, corrupted
state, stale tool surfaces, and unknown errors.

When a tool returns `ok: false`, the coordinator must:

1. Stop all workflow activity.
2. Avoid shell, git, or code workarounds.
3. Present the troubleshooting payload to the user.
4. Wait for user confirmation that the issue is resolved.
5. Retry the same MCP tool.

Unknown errors default to user-actionable.
