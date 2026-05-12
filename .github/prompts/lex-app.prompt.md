---
description: "Create a new Lex app — immediately bootstraps a Lex MCP workflow with kickstart_workflow"
agent: "lex"
argument-hint: "Describe the project you want to build..."
tools: ["lex-mcp-wrapper/*", "read", "edit", "search", "execute", "todo"]
---

You are starting a new Lex MCP workflow. The user's message describes the project they want to build.

**Your FIRST and IMMEDIATE action**: Call `kickstart_workflow` with these parameters extracted from the user's input below:

- `repo_name`: Derive a short kebab-case name from the project description
- `project_overview`: The user's full message (everything after this prompt)
- `private`: true (unless the user says "public")
- `repo_description`: One-sentence summary of the project

Do NOT read files, search the codebase, or ask questions before calling `kickstart_workflow`.

After kickstart completes, call `get_plan_step(step=0)` and proceed through the workflow. End with `finalize_workflow`.
