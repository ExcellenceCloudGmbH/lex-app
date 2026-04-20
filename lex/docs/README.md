---
tags: [lex, planning, copilot, obsidian]
---

# Lex Delivery Playbook

This folder is the immutable handbook for building Lex-compatible financial products.

## Navigation

- [[mcp-execution-model|MCP Execution Model (Single Prompt)]]
- [[planning/README|Planning Steps (guidance for early steps)]]
- [[deployment/README|Deployment Steps (separate topic)]]
- [[runs/run-template|Run Log Template (copy into plans/)]]
- [[lex_topics/00-TOPIC-LIST|Lex Topic Map (Focused Index)]]
- [[lex_topics/99-QUERY-ROUTER|Lex Query Router]]
- [[lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]]
- [[_context/lex_examples/README|Lex Local Example Context Files]]

## Handbook contract

- `docs/` is read-only during planning and implementation runs.
- LLMs must never write generated outputs into `docs/`.
- All generated artifacts must be written to `plans/<run-id>/...`.
- Canonical runtime handbook root for packaged installs is `./.venv/lib/python3.12/site-packages/lex/docs`.
- For LLM execution context, only this handbook root plus a connected `lex-mcp` server are required.
- If this repository also contains a top-level `docs/` folder, treat it as source parity; runtime retrieval should prefer the packaged handbook root.

## MCP step execution contract

- Workflow execution is MCP-driven: step instructions are loaded from MCP via `get_plan_step`, not from local step files.
- Steps 0–14 are served by a single unified tool (`get_plan_step`). There is no separate implementation step tool.
- The IDE LLM acts as a **coordinator** — it calls `get_plan_step`, delegates to the corresponding `lex-step-NN` agent, and calls `notify_step_complete`. It does NOT do step work itself.
- For each step: coordinator loads step → invokes step agent → agent does the work → coordinator notifies completion → coordinator loads next step.
- Human approvals are not required to advance between steps.
- Deployment is intentionally out of scope for this execution mode and is handled separately when explicitly requested.

## How to run

1. Create a run folder under `plans/` (example: `plans/2026-02-24-product-x/`).
2. Copy [[runs/run-template|run-template]] into that run folder as `run.md`.
3. Start workflow initialization from MCP (`kickstart_workflow` or `kickstart_run`), then delegate each step to its `lex-step-NN` agent in order without pausing for approval gates.
4. Each step agent writes its artifacts to `plans/<run-id>/`. The coordinator calls `notify_step_complete` after each step, then loads the next.
5. Continue until all steps are complete in the same prompt execution.
6. Store all generated artifacts only in the active `plans/<run-id>/` folder.
7. Call `finalize_workflow` to merge the workflow branch into main.

## Core system assumptions

- Target code is Python and Django-ish.
- Lex consumes generated files and assembles project structure.
- Django ORM models are the primary source of truth for data and behavior boundaries.
- Business logic must be implementable from pseudocode with minimal ambiguity.

## Lex ground-truth rule

- Always apply [[lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].
- Lex in this project is an implementation platform/framework contract, not a standalone traceability requirement policy.
