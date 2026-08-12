# Lex MCP Modes

Use this file as the canonical quick guide for mode switching across Lex MCP servers.

If you are an AI agent and you need more context before switching modes, read this file first instead of relying on long tool descriptions.

## AI Quickview (Read This First)

Use this table for first-pass mode selection. If user intent matches one row exactly, use that mode immediately.

| User Intent (Plain English) | Correct Mode | Do Not Use | Why |
| --- | --- | --- | --- |
| "Help me work out what I need" / "interview me" / "write the spec or prompt" | `brief` | `forward`, `mvp_generator` | Produces the project contract by interviewing the user. Nothing is built. |
| "Build a new app from scratch" | `forward` | `edit`, `review`, `test`, `backward`, `mvp_completion` | Full end-to-end project creation flow. |
| "Build only an MVP quickly" | `mvp_generator` | `edit`, `review`, `mvp_completion` | Reduced-scope forward workflow for MVP delivery. |
| "Complete the MVP" / "upgrade MVP to full product" | `mvp_completion` | `edit`, `mvp_generator` | Completion mode exists specifically for MVP -> full-product expansion. |
| "Modify existing project" / "implement targeted change" | `edit` | `forward`, `mvp_generator`, `mvp_completion` | Focused edits on an already existing Lex project. |
| "Review/audit existing project" | `review` | `edit`, `forward` | Produces review artifacts, not implementation changes. |
| "The input format changed" / "the columns are different now" / "new CSV layout" / "adapt the app to the new data" | `input` | `edit`, `review` | Migrates the app's hand-written input parsing and downstream code to a changed input-data format. |
| "Write tests" / "test this app" / "are these tests any good" | `test` | `review`, `edit` | Writes the test suite and proves it catches regressions. Requires user stories to exist first. |
| "Document an existing project" / "reverse document" | `backward` | `forward`, `edit`, `review` | Reverse documentation and migration workflow. |

### Priority Disambiguation Rules (AI)

1. If user says "MVP" and "complete", "finish", "expand", or "full product", choose `mvp_completion`.
2. If user says "MVP" and asks to create/build initial version only, choose `mvp_generator`.
3. If user asks for implementation changes in an existing repo and does not mention MVP upgrade, choose `edit`.
4. Never choose `edit` for MVP completion requests.
5. If the user asks for tests, test data, or test coverage, choose `test` — not
   `edit`. Test mode owns the JSON scenario-data paradigm and the effectiveness
   audit; `edit`'s tests task only patches tests for a change it just made.
6. If the user asks whether existing tests are any good, choose `test` — its
   effectiveness audit is the only surface that answers that.
7. If the user reports a changed input-data format (renamed/added/dropped
   columns, new delimiter or encoding, new row grain, restructured upload
   files), choose `input` — NOT `edit`. `edit` is for feature or behavior
   changes; `review` is a read-only audit and changes nothing.
8. If the user is unclear about what they want built, or asks for help planning,
   specifying, or "writing the prompt", choose `brief` — not `forward`. Brief mode
   interviews them and writes `.lex/contract.md`, then hands off. Do not choose
   `brief` when the user already knows what they want and asked you to build it.
9. Ask one clarification question only when intent matches multiple rows.

### Fast Intent Examples

- "Please complete this MVP to production quality" -> `mvp_completion`
- "Generate an MVP for this idea" -> `mvp_generator`
- "Refactor this existing Lex app and add OAuth" -> `edit`
- "Run a code quality audit" -> `review`
- "Write tests for this app" -> `test`
- "Create the test data for these scenarios" -> `test`
- "Do these tests actually catch bugs?" -> `test`
- "Create business and technical docs from existing code" -> `backward`
- "The upload file broke — the columns are different now" -> `input`
- "We get a new CSV layout next month, adapt the app to the new data" -> `input`
- "I want to build something but I'm not sure what I need" -> `brief`
- "Interview me about my project" / "help me write the spec" -> `brief`

## Mode Summary

### `brief`
Purpose: Interview the user and produce the LEX project contract.

Use when:
- The user does not yet have a clear specification, or asks for help producing one.
- You want the outcome, actors, workflow, business rules, boundaries, and
  definition of done captured before any build mode starts.

Shape: flat and conversational — no step loop. The server hands the assistant one
interview topic at a time; the assistant does the talking. Answers are validated
per topic, so a filler answer is refused with a sharper follow-up to ask instead,
and every answer records whether the user decided it or the assistant assumed it.

Output: `.lex/contract.md` (the answers, hand-editable) and `.lex/prompt.md` (a
standalone contract prompt). Nothing is built, committed, or run.

Handoff: the chosen scenario pins the build mode. `finalize_brief` returns it in
`hands_off_to`, and the build mode reads `.lex/contract.md` rather than re-asking.

Primary servers/tools:
- `src/lex_mcp_brief/brief_mcp.py`
- `kickstart_brief`, `get_next_question`, `submit_answer`, `skip_topic`,
  `get_brief_status`, `finalize_brief`
- `open_interview_form` on request, when the user would rather fill in a form
  than answer in the chat

### `forward`
Purpose: Full project creation workflow.

Use when:
- Building a new Lex app from scratch.
- Running the full planning -> implementation -> hardening -> technical-map flow.

Primary servers/tools:
- `src/lex_mcp_local/wrapper_mcp.py`
- `kickstart_workflow`, `get_plan_step`, `notify_step_complete`, `finalize_workflow`

### `backward`
Purpose: Reverse documentation and migration workflow for existing projects.

Use when:
- Project already exists and needs structured documentation.
- You need scanner/wiki/questionnaire/business docs generation.

Primary servers/tools:
- `src/lex_mcp_reverse/reverse_mcp.py`
- `reverse_kickstart`, `scan_project`, `generate_wiki`, `generate_questionnaire`, `submit_questionnaire`

### `edit`
Purpose: Targeted modification workflow for existing Lex projects.

Use when:
- Applying focused changes to an existing Lex app.
- Running a task-catalog driven edit session with explicit artifacts.

Primary servers/tools:
- `src/lex_mcp_edit/edit_mcp.py`
- `kickstart_edit`, `list_edit_tasks`, `propose_edit_plan`, `set_edit_plan`, `get_task_brief`, `notify_task_complete`, `finalize_edit`

### `review`
Purpose: Static review/audit workflow for existing Lex projects.

Use when:
- Running compliance, architecture, code-quality, or risk reviews.
- You need review reports, not implementation changes.

Primary servers/tools:
- `src/lex_mcp_review/review_mcp.py`
- `kickstart_review`, `list_review_types`, `get_review_brief`, `notify_review_complete`, `finalize_review`

### `test`
Purpose: Write the test suite for an existing Lex project and prove it works.

Use when:
- Writing tests, test scenarios, or test data for a Lex app.
- Auditing whether an existing suite would actually catch a regression.
- Setting up `lex_test_config.yaml` group selection.

Shape: flat and autonomous, like `review` — no step loop. The orchestrator is
handed every responsibility test mode owns and picks the ones the project needs.

Hard prerequisite: the project must already have LEX-AI-style user stories
(`plans/technical_docs/step-03-user-stories.md` from forward, or
`plans/business_docs/business/03-user-journeys.md` from backward). Every
assertion has to trace back to a stated acceptance criterion, so
`kickstart_test_run` refuses without them and `dispatch_reverse_prerequisite`
runs the backward workflow first, then hands control back to test mode.

Primary servers/tools:
- `src/lex_mcp_test/testing_mcp.py`
- `kickstart_test_run`, `dispatch_reverse_prerequisite`, `list_responsibilities`,
  `propose_test_plan`, `set_test_plan`, `get_responsibility_brief`,
  `record_scenario_matrix`, `notify_responsibility_complete`,
  `waive_responsibility`, `record_test_execution`, `get_test_status`,
  `finalize_test_run`

### `input`
Purpose: Adapt an existing Lex App to a changed input-data format.

Use when:
- The input files changed shape: renamed, added, or dropped columns, a new
  delimiter or encoding, a new row grain, or restructured upload files.
- The app's upload parsing no longer matches the incoming data. A Lex App's
  input parsing is hand-written pandas code inside upload models'
  `calculate()` methods, so a format change is a code migration.

Shape: flat like `review` and `edit` — no coordinator loop and no mandatory
ordering; `kickstart_input_change` is the only mandatory first call. Intake is
dual-path: the user provides a sample file in the new format (CSV/TSV headers
sniffed server-side, XLSX inspected by the analyst agent) or describes the
change verbally, in which case the `lex-input-analyst` agent interviews the
user until the old-vs-new column mapping is unambiguous.

Output: `input-changes/` in the project (format spec, per-area change logs,
`_index.md`, and `INPUT-CHANGE-REPORT.md` written by finalize). Session state
lives in `.lex-input/manifest.json`. `finalize_input_change` warns when the
recommended areas (upload-parser, migrations, test-data) never completed.

Primary servers/tools:
- `src/lex_mcp_input/input_mcp.py`
- `kickstart_input_change`, `register_format_change`, `get_intake_brief`,
  `list_change_areas`, `get_change_brief`, `notify_change_complete`,
  `get_input_change_status`, `finalize_input_change`

### `mvp_generator`
Purpose: Reduced-scope forward workflow focused on MVP delivery.

Use when:
- Building a minimal viable product with a constrained step sequence.
- You need faster delivery with narrower implementation scope.

Primary servers/tools:
- `src/lex_mcp_mvp/mvp_mcp.py`
- `kickstart_mvp`, `list_mvp_checkpoints`, `submit_mvp_plan`, `submit_mvp_scope`, `scaffold_mvp`, `record_implementation`, `run_boot_checklist`, `finalize_mvp`

### `mvp_completion`
Purpose: Upgrade an MVP project to final/full-product scope.

Use when:
- MVP exists and you want completion/expansion to full product quality.
- You need the completion-mode orchestration and wiki sync phases.

Primary servers/tools:
- `src/lex_mcp_mvp_completion/wrapper_mcp.py`
- Completion variants of `kickstart_workflow`, `get_plan_step`, `notify_step_complete`, `finalize_workflow`

## Switching Guidance

1. Confirm user intent explicitly before switching modes.
2. Quote the user intent in the switch tool's `user_request` argument.
3. Use `force=True` only when a guarded transition explicitly requires it and the user approved.
4. After switching, wait for tools to refresh before calling new-mode tools.

## Notes For AI Agents

- Prefer reading this file when deciding mode transitions.
- Keep switch tool descriptions short and defer detailed explanation to this document.
- Do not switch modes just to bypass errors; resolve workflow requirements first.
