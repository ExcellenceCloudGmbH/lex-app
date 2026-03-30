---
tags: [implementation, lex, copilot]
---

# Implementation Phase (Step 0-11)

This phase converts approved planning artifacts into production-ready code and verified outputs.

## Start here (single entry point)

1. Open and copy: [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]].
2. Paste it as your first user message for implementation.
3. Let Copilot create `plans/<run-id>/implementation/run.md` and begin Step 0.
4. Apply [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].

## Input contract

- Implementation starts only after planning Step 8 is approved.
- Read planning artifacts from `plans/<run-id>/step-00..step-08*.md`.
- Treat planning outputs as the source of truth unless user explicitly revises requirements.

## Output destination rule

- `docs/` files are handbook-only and must not be edited during runs.
- All implementation outputs must be created in `plans/<run-id>/implementation/`.

## MCP step retrieval contract

- Do not read implementation step instructions from local files during execution.
- Retrieve each step from MCP using `get_implementation_step`.
- Request and complete exactly one step at a time.
- After finishing Step `N`, make a new MCP request for Step `N+1`.
- Implementation starts only after planning is fully complete and approved.
- Implementation must complete all steps (`0..11`) before deployment starts.

## Step order

0. [[00-implementation-kickoff|Implementation Kickoff and Scope Lock]]
1. [[01-foundation-and-scaffolding|Foundation and Scaffolding]]
2. [[02-model-layer-implementation|Model Layer Implementation]]
3. [[03-ingestion-and-validation|Ingestion and Validation Pipeline]]
4. [[04-calculation-and-business-services|Calculation and Business Services]]
5. [[05-api-workflows-and-permissions|API Workflows and Permissions]]
6. [[06-tests-reconciliation-and-hardening|Reconciliation and Hardening]]
7. [[07-full-project-implementation|Implementation Blueprint Consolidation]]
8. [[08-release-readiness-and-handover|Release and Handover Planning Gate]]
9. [[09-rule-compliance-validation|Rule Compliance Validation for Implementation Plans]]
10. [[10-full-project-implementation|Full Project Implementation (Code Delivery)]]
11. [[11-code-rule-compliance-validation|Code-Level Lex Rule Compliance Validation (Final Gate)]]

## Step routing map (where to go next)

- After Step 0 output: update `plans/<run-id>/implementation/step-00-kickoff.md`, then continue to Step 1.
- After Step 1 output: update `plans/<run-id>/implementation/step-01-foundation.md`, then continue to Step 2.
- After Step 2 output: update `plans/<run-id>/implementation/step-02-models.md`, then continue to Step 3.
- After Step 3 output: update `plans/<run-id>/implementation/step-03-ingestion.md`, then continue to Step 4.
- After Step 4 output: update `plans/<run-id>/implementation/step-04-services.md`, then continue to Step 5.
- After Step 5 output: update `plans/<run-id>/implementation/step-05-api.md`, then continue to Step 6.
- After Step 6 output: update `plans/<run-id>/implementation/step-06-quality.md`, then continue to Step 7.
- After Step 7 output: update `plans/<run-id>/implementation/step-07-blueprint.md`, then continue to Step 8.
- After Step 8 output: update `plans/<run-id>/implementation/step-08-release-plan.md`, then continue to Step 9.
- After Step 9 output: update `plans/<run-id>/implementation/step-09-plan-rule-validation.md`, then continue to Step 10.
- After Step 10 output: update `plans/<run-id>/implementation/step-10-implementation.md`, then continue to Step 11.
- After Step 11 output: update `plans/<run-id>/implementation/step-11-code-rule-validation.md`; require explicit final implementation approval before deployment.

## Mandatory implementation outputs

- Traceability map from planning requirements/stories to code modules
- Implemented ORM model layer aligned to approved planning
- Implemented ingestion, validation, and calculation services
- Implemented API/workflow behaviors and permission boundaries
- Approved implementation plan bundle that is Lex-rule-compliant before coding
- Complete code artifacts implementing the approved project scope
- Reconciliation evidence and release checklist with approval
- Final code-level rule compliance validation against [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]]

## Session templates

- [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]]
- [[templates/copilot-session-template|Copilot Session Template]]
- [[templates/hitl-step-template|HITL Step Template]]
- [[templates/llm-response-structure|LLM Response Structure (Mandatory)]]

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
- Gate progression with explicit `Approved: Yes/No` at required checkpoints.
