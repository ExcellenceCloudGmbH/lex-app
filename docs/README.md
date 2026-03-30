---
tags: [lex, planning, copilot, obsidian]
---

# Lex Delivery Playbook

This folder is the immutable handbook for building Lex-compatible financial products.

## Navigation

- [[planning/README|Planning Phase (Step 0-8)]]
- [[implementation/README|Implementation Phase (Step 0-11)]]
- [[deployment/README|Deployment Phase (Step 0-8)]]
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

- Planning, implementation, and deployment steps are retrieved from the MCP server, not read directly from local phase files during execution.
- Use `get_planning_step` for planning steps, `get_implementation_step` for implementation steps, and `get_deployment_step` for deployment steps.
- Execute exactly one step at a time: request one step from MCP, complete that step, then request the next step in a new MCP call.
- Enforce phase order strictly: finish planning (`0..8`) before implementation (`0..11`), and finish implementation before deployment (`0..2`).

## How to run

1. Create a run folder under `plans/` (example: `plans/2026-02-24-product-x/`).
2. Copy [[runs/run-template|run-template]] into that run folder as `run.md`.
3. Work through planning steps in order: `00` to `08` using handbook docs in `docs/planning/`.
4. Do not move to the next step unless the current step contains an explicit `Approved` decision.
5. After final planning validation approval, continue with implementation steps in `docs/implementation/`.
6. Store all generated artifacts only in the active `plans/<run-id>/` folder.

## Core system assumptions

- Target code is Python and Django-ish.
- Lex consumes generated files and assembles project structure.
- Django ORM models are the primary source of truth for data and behavior boundaries.
- Business logic must be implementable from pseudocode with minimal ambiguity.

## Lex ground-truth rule

- Always apply [[lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].
- Lex in this project is an implementation platform/framework contract, not a standalone traceability requirement policy.
