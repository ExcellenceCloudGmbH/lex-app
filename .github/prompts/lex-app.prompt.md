---
description: "Create a new Lex app — immediately bootstraps a Lex MCP workflow with kickstart_workflow"
argument-hint: "Describe the project you want to build..."
tools: ["lex-mcp-local/*", "read", "edit", "search", "execute", "todo", "agent"]
---

You are starting a new Lex MCP workflow. The user's message describes the project they want to build.

**Your FIRST and IMMEDIATE action**: Call `kickstart_workflow` with these parameters extracted from the user's input below:

- `repo_name`: Derive a short kebab-case name from the project description
- `project_overview`: The user's full message (everything after this prompt)
- `private`: true (unless the user says "public")
- `repo_description`: One-sentence summary of the project

Do NOT read files, search the codebase, or ask questions before calling `kickstart_workflow`.

After kickstart completes, you are the **coordinator**. Call `get_plan_step(step=0)`, delegate to the `lex-step-00` agent, then proceed through all 20 forward steps (0–19) using the coordinator loop: get_plan_step → delegate to agent → notify_step_complete → next step. When step 19 is done, call `finalize_workflow()`, write the required audit report, then call `finalize_workflow(audit_complete=True)`.
