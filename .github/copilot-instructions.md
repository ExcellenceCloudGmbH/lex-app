# Lex MCP Workspace Rules

## FIRST-ACTION RULE — Lex App Requests

When the user asks to **create**, **build**, **start**, **bootstrap**, or **develop** a **Lex app**, **Lex application**, or any project **using the Lex MCP server**, you MUST call `kickstart_workflow` as your **absolute first action** — before ANY file reads, searches, explorations, clarifying questions, or other tool calls.

**This rule is non-negotiable and takes priority over all other behaviors.**

### How to extract parameters

1. **`repo_name`** — Derive a short `kebab-case` name from the project description in the user's message. If the user explicitly names the project, use that name.
2. **`project_overview`** — Pass the user's **entire message verbatim** as the project overview. This gives the workflow complete context from the start.
3. **`organization`** — Use the organization name if the user mentions one. Otherwise omit.
4. **`private`** — Default `true` unless the user explicitly says "public".
5. **`repo_description`** — Summarize the project purpose in one sentence from the user's message.

### Do NOT

- Do NOT read files, search the codebase, or explore the workspace before calling `kickstart_workflow`.
- Do NOT ask clarifying questions. Use sensible defaults for anything not specified.
- Do NOT call any other Lex MCP tool before `kickstart_workflow`.

### Decision: new project vs existing project

| Signal in user's message                                  | Tool to call         |
| --------------------------------------------------------- | -------------------- |
| "create", "build", "new", "bootstrap", "start a lex app"  | `kickstart_workflow` |
| "continue", "update", "add feature to", "modify existing" | `kickstart_run`      |

When ambiguous, default to `kickstart_workflow`.

## Mandatory Workflow Pattern

Every Lex workflow follows this sequence without exception:

```
kickstart_workflow OR kickstart_run  →  [work]  →  finalize_workflow
```

See the tool descriptions for step details. Steps (0–12) are optional guidance — the kickstart → finalize pattern is mandatory.
