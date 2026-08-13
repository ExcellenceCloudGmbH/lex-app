---
description: "Resume a Lex workflow — picks up where you left off in a previous session"
argument-hint: "Repo name (optional — will auto-detect from local git if omitted)"
tools: ["lex-mcp-local/*", "read", "edit", "search", "execute", "todo", "agent"]
---

You are resuming an existing Lex MCP workflow from a previous session.

**Your actions, in order:**

1. **Call `resume_workflow`** to reconstruct context from the previous session.
   - If the user provided a repo name, pass it as `repo`.
   - If not, call with no arguments — the tool will attempt to infer from the local `.git` config.
   - If `organization` is needed and not provided, the tool will return available options for the user to select.

2. **Call `get_workflow_status`** to get a compact summary of progress: completed steps, current branch, uncommitted changes, and suggested next action.

3. **Read step 0** via `get_plan_step(step=0)` to re-orient yourself on the project conventions and docs structure.

4. **Present a summary to the user**:
   - Which project was resumed
   - Which steps are complete
   - What the next step is
   - Whether there are uncommitted changes

5. **Load the next incomplete step** and continue the coordinator loop: get_plan_step → delegate to step agent → notify_step_complete → next step.

6. **When all work is done**, call `finalize_workflow()`, write the required audit report, then call `finalize_workflow(audit_complete=True)`.

Do NOT ask the user which step to resume from — the system tracks this automatically via the `.lex-workflow/manifest.json` manifest.
