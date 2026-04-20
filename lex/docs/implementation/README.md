---
tags: [implementation, lex, copilot]
---

# Implementation Steps (Part of Unified Step Flow)

> **Important:** Implementation is NOT a separate process with its own MCP tool.
> Steps 9–14 of the unified `get_plan_step` tool cover implementation work.
> There is no `get_implementation_step` tool.

These steps convert planning artifacts into production-ready code and run immediately after the planning steps (0–8) in the same workflow execution.

## Start here (single entry point)

1. Complete planning Steps 0–8 via `get_plan_step`.
2. Continue directly with `get_plan_step(step=9)`.
3. Apply [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].

## Input contract

- Implementation starts immediately after planning Step 8 completes.
- Read planning artifacts from `plans/<run-id>/step-00..step-08*.md`.
- Treat planning outputs as the source of truth unless user explicitly revises requirements.

## Output destination rule

- `docs/` files are handbook-only and must not be edited during runs.
- All implementation outputs must be created in `plans/<run-id>/`.

## MCP step retrieval contract

- Load each step from MCP using `get_plan_step` (the same tool used for planning steps).
- Execute steps sequentially: load Step `N`, complete Step `N`, persist outputs, call `notify_step_complete(step=N, process='planning')`, then load Step `N+1`.
- Do not wait for approval gates between steps.
- Implementation must complete all steps (9–14) before any deployment work begins.

## Mandatory implementation outputs

- Traceability map from planning requirements/stories to code modules
- Implemented ORM model layer aligned to approved planning
- Implemented ingestion, validation, and calculation services
- Implemented API/workflow behaviors and permission boundaries
- Complete code artifacts implementing the approved project scope
- Reconciliation evidence and release checklist
- Final code-level rule compliance validation against [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]]

## Non-negotiable rules for Copilot

- Implement only what is approved in planning unless user explicitly changes scope.
- Keep traceability from requirement IDs/story IDs to implementation artifacts.
- Treat `LexModel` and `CalculationModel` as preimplemented framework internals (use/subclass only; do not re-implement).
- Treat Lex as an implementation framework contract, not a standalone traceability policy.
- Enforce `Inputs`, `Uploads`, and `Reports` as distinct functional modules.
- Ensure every report model defines at least one Django `FileField`.
- Apply CSV-only I/O assumptions unless user explicitly overrides.
- Do not generate Django bootstrap/scaffold files (`apps.py`, `urls.py`, `settings.py`, etc.) unless explicitly requested.
- Load Lex runtime context from `docs/lex_topics/21-LEX-APP-CONTEXT.yaml` whenever framework-level behavior details are needed.
- Ask for missing inputs instead of inventing schema, workflow, or policy details.
- Complete each step fully, persist outputs, and advance immediately using MCP step sequencing.
