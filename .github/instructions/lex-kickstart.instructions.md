---
description: "Use when: the user asks to create a lex app, build a lex application, bootstrap a project with the Lex MCP server, start a new lex project, kickstart a lex workflow, or mentions 'lex app', 'lex mcp', 'lex application', 'lex project', 'lex server'. Covers the mandatory first-action rule for Lex MCP workflows."
---

# Lex MCP Kickstart — First-Action Rule

When ANY of these phrases appear in the user's message, you MUST call `kickstart_workflow` IMMEDIATELY as your absolute first tool call:

- "lex app"
- "lex application"
- "lex project"
- "lex mcp"
- "using the lex mcp server"
- "create a lex"
- "build a lex"
- "start a lex"
- "bootstrap a lex"

## What "immediately" means

- Do NOT read any files first.
- Do NOT search the codebase first.
- Do NOT explore the workspace first.
- Do NOT ask clarifying questions first.
- Your very first tool call MUST be `kickstart_workflow` (new project) or `kickstart_run` (existing project).

## Parameter extraction from the user's message

| Parameter          | How to extract                                                                                          |
| ------------------ | ------------------------------------------------------------------------------------------------------- |
| `repo_name`        | Derive a short `kebab-case` name from the project description. If the user names the project, use that. |
| `project_overview` | Pass the user's **entire message** verbatim.                                                            |
| `organization`     | Use if the user mentions a GitHub org. Otherwise omit.                                                  |
| `private`          | Default `true`. Only set `false` if the user explicitly says "public".                                  |
| `repo_description` | One-sentence summary of the project purpose from the user's message.                                    |

## Decision: which kickstart to call

| Signal in user's message                                  | Tool                 |
| --------------------------------------------------------- | -------------------- |
| "create", "build", "new", "bootstrap", "start"            | `kickstart_workflow` |
| "continue", "update", "add feature to", "modify existing" | `kickstart_run`      |

When ambiguous, default to `kickstart_workflow`.

## After kickstart completes

Follow the mandatory workflow pattern:

1. Call `get_plan_step(step=0)` to begin.
2. Execute steps, committing with `notify_step_complete`.
3. End with `finalize_workflow` — this is non-negotiable.

## Git Operations — HANDS OFF

All git operations are handled by the Lex MCP backend. **Do NOT run git commands yourself.** The backend commits, pushes, and manages branches automatically.

If any MCP tool call returns `"reason_for_failure": "git_issue"`, read the `git_issue` trace, resolve the problem, and then **re-call the same MCP tool that failed**. Do not skip it.
