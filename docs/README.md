---
tags: [lex, planning, copilot, obsidian]
---

# Lex Delivery Playbook

This folder is the immutable handbook for building Lex-compatible financial products.

## Navigation

- [[mcp-execution-model|MCP Execution Model (Single Prompt)]]
- [[planning/README|Planning Steps (0-8)]]
- [[implementation/README|Implementation Steps (0-11)]]
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

- Workflow execution is MCP-driven: step instructions are loaded from MCP, not from local step files.
- The default run mode is single-prompt sequential execution: planning (`0..8`) then implementation (`0..11`) in one continuous prompt execution.
- For each step: load current step → execute work → write artifacts under `plans/<run-id>/...` → notify completion → load next step.
- Human approvals are not required to advance between planning and implementation steps.
- Deployment is intentionally out of scope for this execution mode and is handled separately when explicitly requested.

## How to run

1. Create a run folder under `plans/` (example: `plans/2026-02-24-product-x/`).
2. Copy [[runs/run-template|run-template]] into that run folder as `run.md`.
3. Start workflow initialization from MCP, then execute planning and implementation steps in order without pausing for approval gates.
4. Persist each step artifact as soon as that step completes, then notify completion before loading the next step.
5. Continue until all planning and implementation steps are complete in the same prompt execution.
6. Store all generated artifacts only in the active `plans/<run-id>/` folder.

## Core system assumptions

- Target code is Python and Django-ish.
- Lex consumes generated files and assembles project structure.
- Django ORM models are the primary source of truth for data and behavior boundaries.
- Business logic must be implementable from pseudocode with minimal ambiguity.

## Lex ground-truth rule

- Always apply [[lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].
- Lex in this project is an implementation platform/framework contract, not a standalone traceability requirement policy.
