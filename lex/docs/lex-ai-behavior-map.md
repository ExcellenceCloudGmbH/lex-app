OUTDATED
# Lex AI Behavior Map

This file maps the intended workflow for the main Lex AI task shapes. It is code-anchored to the current MCP implementation, not older handbook wording.

## Shared Mode Rules

- Lex AI exposes one MCP mode at a time in unified mode.
- Forward mode is for planning, implementation, hardening, wiki enrichment, and final forward/backward sync.
- Backward mode is for scanning an existing project, building `technical-map/`, generating business-facing docs, and running the reverse questionnaire flow.
- Backward mode does not modify application code.
- Moving from backward mode to forward mode can happen when the user wants to stop documenting and start changing code.
- Moving from forward mode to backward mode is guarded because it can abandon an in-progress forward run.

## 1. New Lex AI Project

- User explicitly asks to create a new Lex AI app or start a new Lex MCP workflow.
- If the MCP is currently in backward mode, switch to forward mode first.
- The coordinator calls `kickstart_workflow(...)` as the first workflow action.
- The server validates GitHub credentials, resolves the target account or organization, and creates the GitHub repository.
- The server initializes local git, writes `AGENTS.md`, pushes the initial commit to the default branch, creates a workflow branch `{repo}/run-XX`, writes `.lex-workflow/manifest.json`, and opens a workflow tracking issue.
- The coordinator starts the strict step loop with `get_plan_step(step=0)`.
- The coordinator delegates every forward step in order through `notify_step_complete(...)`.
- Steps 0–8 build the planning artifacts.
- Steps 9–11 implement the code.
- Steps 12–14 harden the result.
- Steps 15–18 generate and enrich `technical-map/`.
- Step 19 cross-checks forward docs, backward docs, and code when both doc sets exist.
- Each `notify_step_complete(...)` call commits and pushes the current step to the workflow branch.
- The first `finalize_workflow()` call returns audit instructions.
- The coordinator writes `plans/technical_docs/audit-report.md`.
- The second `finalize_workflow(audit_complete=True)` call commits any remaining changes, creates a PR to the default branch, and currently squash-merges it immediately.
- The workflow ends with the local checkout back on the default branch and the tracking issue closed.
- Current implementation note: the workflow does not wait for manual PR review after opening the PR.

## 2. New Feature Inside an Existing Lex Project

- User wants to add or modify behavior in an existing Lex project.
- If the project is poorly understood or undocumented, start in backward mode to map the current system before changing code.
- In backward mode, call `reverse_kickstart(...)` on the existing project path or GitHub URL.
- Run `scan_project()` to generate the R-00 to R-02 scan artifacts.
- Run `generate_wiki()` to build `technical-map/` and per-module `CONTEXT.md` files.
- Run the reverse agent loop for steps 3–6 to review and enrich the wiki.
- Run `generate_questionnaire()` and pause for the user to validate `[LLM-FILLED]` answers and fill `[USER-REQUIRED]` placeholders.
- Run `submit_questionnaire(...)` when the questionnaire is complete.
- Run reverse steps 8–16 to generate the business-facing documentation set.
- Run `finalize_reverse()` and then complete reverse step 17.
- When the user wants actual code changes, switch to forward mode.
- In forward mode, call `kickstart_run(...)` on the existing repository.
- The server fetches the repo, creates a fresh workflow branch and tracker issue, and marks the run as an existing-project run.
- The coordinator still starts from `get_plan_step(step=0)` and executes the full forward step loop.
- Steps 0–10 re-plan the delta for the requested feature.
- Step 11 routes to `lex-step-11-refactor` instead of the greenfield implementation agent.
- Steps 12–19 harden, enrich docs, and sync technical and business artifacts.
- The run ends with the same two-stage `finalize_workflow(...)` audit and PR flow as a new project.
- Capability boundary: backward mode builds understanding and documentation; forward mode performs the code changes.

## 3. Revision to an Existing Planning Document

- User changes one or more forward planning artifacts and wants Lex AI to reconfigure the plan and code.
- Use forward mode for this task.
- If the workflow is already in progress, call `resume_workflow(...)` and then `get_workflow_status()`.
- If there is no active run but the project already exists, start a new change run with `kickstart_run(...)`.
- On the next `get_plan_step(step=N)` call, the server checks the working tree for user edits.
- The server maps changed files back to the step that originally created them using `.lex-workflow/manifest.json`.
- If earlier-step files changed, the tool returns `user_changes_detected` with the earliest affected step and the original target step.
- The server attempts to auto-commit and push the user's planning-document edits before re-execution.
- The coordinator re-runs from the affected step through the original target step instead of freelancing a partial update.
- If the user added new `.csv` or `.xlsx` files, re-execution begins from step 2 because IO assumptions changed.
- On existing-project runs, step 11 uses the refactor agent so the code is updated incrementally rather than regenerated from scratch.
- After the re-execution catches up, the coordinator continues through the remaining forward steps.
- The run finishes with the same audit, PR, and auto-merge flow as any other forward run.

## 4. Backward Documentation for an Existing Project

- User wants documentation, reverse mapping, migration context, `technical-map/`, or business-facing docs for an existing project.
- If the MCP is currently in forward mode, switch to backward mode first.
- The coordinator calls `reverse_kickstart(project_path=... or github_url=...)`.
- If `.lex-reverse/manifest.json` already exists and `force_new` is not set, the reverse run resumes automatically.
- `scan_project()` writes the R-00 to R-02 scan artifacts under `plans/business_docs/`.
- `generate_wiki()` writes the `technical-map/` structure and per-module `CONTEXT.md` files.
- Reverse steps 3–6 enrich the technical wiki.
- `generate_questionnaire()` writes `plans/business_docs/discovery-questionnaire.md` and pauses the workflow.
- The user validates the prefilled answers and fills all remaining `[USER-REQUIRED]` placeholders.
- `submit_questionnaire(...)` validates that the questionnaire is complete.
- Reverse steps 8–16 generate the business documentation set.
- `finalize_reverse()` verifies prerequisites and prepares the final gap-report handoff.
- Reverse step 17 writes the final gap report, and `notify_reverse_complete(step=17, ...)` marks the workflow complete.
- Reverse workflows do not create or merge pull requests because they are documentation workflows, not code-delivery workflows.however