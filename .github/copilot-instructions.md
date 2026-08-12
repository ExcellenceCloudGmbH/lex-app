# Lex MCP Workspace Rules

## Coordinator-Agent Architecture

This project uses a **coordinator-agent** pattern for Lex MCP workflows:

- **Coordinator** (you, the IDE LLM): Manages the step loop — kickstart → get_plan_step → delegate → notify → next step → finalize. You do NOT do step work yourself.
- **Step agents** (`lex-step-00` through `lex-step-19`, plus `lex-step-11-refactor` on existing-project runs): Specialized agents that execute the actual work for each step. Each agent knows exactly what its step requires.
- **Why**: This keeps your context window lean while ensuring each step gets thorough, focused execution.

When the user asks for any Lex workflow, you act as the coordinator. Call `get_plan_step(step=N)`, invoke the corresponding step agent, and use its output to call `notify_step_complete`. Repeat through all 20 forward steps (0–19).

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
kickstart_workflow OR kickstart_run  →  [delegate steps 0–19 to agents]  →  finalize_workflow()  →  write audit report  →  finalize_workflow(audit_complete=True)
```

You are the **coordinator** — call `get_plan_step`, delegate to the step agent, call `notify_step_complete`, and immediately proceed to the next step. Steps are mandatory, not optional. See the tool descriptions for step details.

## Stale Tool-List Recovery (Mode Cache)

Some IDEs cache MCP tools aggressively after a mode switch. If you call a tool
that is no longer active, the server now returns a structured payload with
`ok: false` and `stale_tool_call: true` instead of a raw method-not-found
error.

When this happens:

1. Stop and show the troubleshooting payload to the user.
2. Refresh tools/list.
3. If `suggested_mode` is present, call `switch_to_mode(target_mode="...")`.
4. Retry the original tool call.

The switch response may include `tool_surface_epoch`; treat that as the latest
tool-surface version and prefer fresh tool discovery before continuing.

## Reading a data file — delegate it, never read it yourself

The user's input data arrives as whatever they have: `.csv`, `.tsv`, `.xlsx`,
`.xlsm`, `.xls` or `.pdf`. No format is privileged.

**Invoke the `lex-spreadsheet-reader` sub-agent, one invocation per file.** It
returns a short report: the sheet inventory, the verbatim column names, observed
types, number formats, and the structural traps that silently break a parser —
merged headers, formulas with no cached value, percentages stored as fractions,
totals rows, hidden sheets.

Delegate rather than read because the numbers are brutal. A 100k-row workbook is
around six million characters; pulling that through your own context costs you
the room you needed for the actual work, and the sub-agent hands you back about
eighty lines instead.

- Build your schema from the report, and carry its `sha256` so the schema cites
  an exact file version.
- Treat its "Open questions" list as questions **for the user** — whether a
  column is always populated, the full set of allowed values, whether a key is
  unique. Ask; do not guess.
**For a PDF, invoke `lex-document-reader` instead.** PDFs do not yield to the
same treatment: a scan or a chart has no text to extract, so the sub-agent looks
at rendered page images itself and reports what it found. Two things from its
report matter to you:

- Figures it marks `transcribed (unverified)` were read off pixels. Never record
  one as an exact number without the user confirming it, and never let one become
  a column type, an allowed value, or a test's expected value.
- Its "Verdict for ingestion" is a project decision, not a detail. A PDF with no
  text layer **cannot be parsed by the app** — a Lex upload parser is pandas
  code, and pandas has no PDF reader. Put the choice to the user: a
  machine-readable export, or extraction scoped as real work with its own
  accuracy criteria. Do not let a successful read imply the problem is solved.

The sub-agent has `lex-mcp-local/read_input_file` for this, so it works with no
terminal. Never write a throwaway pandas script and read a schema back out of
stdout: that truncates, it fails silently where there is no shell, and it puts
the whole file in context when you only needed its shape.


## When you cannot know something: ask, do not guess

You are the only actor here who can talk to the user. A sub-agent that hits
something it cannot know returns the question to you, and if you do not put it to
the user it becomes a guess with nobody's name on it.

`lex-mcp-local/ask_user_question` puts one question to them. Where the host can
render a dialog the answer comes back inside that same call and is recorded as
their own words; where it cannot, you get a form link and a question to relay,
and you close it with `lex-mcp-local/record_user_answer`. Ask one decision per
call, offer two to four options that each state their own consequence, and put
your recommendation first.

Ask **at the moment it comes up**, not at the end. A question saved for the final
report is answered after the work is finished, when acting on it costs a rewrite
— which is exactly why it then gets ignored. And carry every answer into the next
brief: each agent starts with an empty context, so an answer left in this
conversation is one the next agent will re-guess differently.

If the user will not or cannot answer, say so honestly with
`record_user_answer(declined=true, assumption='<what you will assume>')`. A
disclosed gap is a fact. A silent default is a claim nobody made.

Never pass your own inference as though the user said it, and never raise a
permission or an authority level by default. Your host loads the full doctrine as
a rule alongside these instructions.
