# Lex MCP Modes

Use this file as the canonical quick guide for mode switching across Lex MCP servers.

If you are an AI agent and you need more context before switching modes, read this file first instead of relying on long tool descriptions.

## AI Quickview (Read This First)

Use this table for first-pass mode selection. If user intent matches one row exactly, use that mode immediately.

| User Intent (Plain English) | Correct Mode | Do Not Use | Why |
| --- | --- | --- | --- |
| "Build a new app from scratch" | `forward` | `edit`, `review`, `backward`, `mvp_completion` | Full end-to-end project creation flow. |
| "Build only an MVP quickly" | `mvp_generator` | `edit`, `review`, `mvp_completion` | Reduced-scope forward workflow for MVP delivery. |
| "Complete the MVP" / "upgrade MVP to full product" | `mvp_completion` | `edit`, `mvp_generator` | Completion mode exists specifically for MVP -> full-product expansion. |
| "Modify existing project" / "implement targeted change" | `edit` | `forward`, `mvp_generator`, `mvp_completion` | Focused edits on an already existing Lex project. |
| "Review/audit existing project" | `review` | `edit`, `forward` | Produces review artifacts, not implementation changes. |
| "Document an existing project" / "reverse document" | `backward` | `forward`, `edit`, `review` | Reverse documentation and migration workflow. |

### Priority Disambiguation Rules (AI)

1. If user says "MVP" and "complete", "finish", "expand", or "full product", choose `mvp_completion`.
2. If user says "MVP" and asks to create/build initial version only, choose `mvp_generator`.
3. If user asks for implementation changes in an existing repo and does not mention MVP upgrade, choose `edit`.
4. Never choose `edit` for MVP completion requests.
5. Ask one clarification question only when intent matches multiple rows.

### Fast Intent Examples

- "Please complete this MVP to production quality" -> `mvp_completion`
- "Generate an MVP for this idea" -> `mvp_generator`
- "Refactor this existing Lex app and add OAuth" -> `edit`
- "Run a code quality audit" -> `review`
- "Create business and technical docs from existing code" -> `backward`

## Mode Summary

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
