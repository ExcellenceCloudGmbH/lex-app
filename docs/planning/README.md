---
tags: [planning, lex, copilot]
---

# Planning Phase (Step 0-8)

This phase produces complete implementation-ready specs before coding starts.

## Start here (single entry point)

1. Open and copy: [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]].
2. Paste it as your first user message to Copilot.
3. Let Copilot create `plans/<run-id>/run.md` and begin Step 0.
4. Apply [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].

## Output destination rule

- `docs/` files are handbook-only and must not be edited during runs.
- All planning outputs must be created in `plans/<run-id>/`.

## MCP step retrieval contract

- Do not read planning step instructions from local files during execution.
- Retrieve each step from MCP using `get_planning_step`.
- Request and complete exactly one step at a time.
- After finishing Step `N`, make a new MCP request for Step `N+1`.
- Planning must complete all steps (`0..8`) before implementation starts.

## Step order

0. [[00-project-overview|Project Overview]]
1. [[01-requirements|Requirements and End Goals]]
2. [[02-io-csv-schemas|Input/Output CSV Schemas]]
3. [[03-architecture-and-flow|Project Structure and Data Flow]]
4. [[04-user-stories|User Stories (HITL-heavy)]]
5. [[05-functional-breakdown|Functional Breakdown and Information Mapping]]
6. [[06-uml-er-diagrams|UML + ER Diagrams and Approval Gate]]
7. [[07-business-logic-pseudocode|Business Logic Pseudocode and Approval Gate]]
8. [[08-rule-compliance-validation|Rule Compliance Validation and Final Planning Gate]]

## Step routing map (where to go next)

- After Step 0 output: update `plans/<run-id>/step-00-overview.md`, then continue to Step 1.
- After Step 1 output: update `plans/<run-id>/step-01-requirements.md`, then continue to Step 2.
- After Step 2 output: update `plans/<run-id>/step-02-io-schemas.md`, then continue to Step 3.
- After Step 3 output: update `plans/<run-id>/step-03-architecture.md`, then continue to Step 4.
- After Step 4 output: update `plans/<run-id>/step-04-user-stories.md`, then continue to Step 5.
- After Step 5 output: update `plans/<run-id>/step-05-functional-breakdown.md`, then continue to Step 6.
- After Step 6 output: update `plans/<run-id>/step-06-uml-er.md`; require explicit approval before Step 7.
- After Step 7 output: update `plans/<run-id>/step-07-pseudocode.md`; require explicit approval before Step 8.
- After Step 8 output: update `plans/<run-id>/step-08-rule-validation.md`; require explicit final planning approval before implementation.
- After final planning approval: continue with [[../implementation/README|Implementation Phase (Step 0-11)]].

## Mandatory planning outputs

- Approved requirements list
- Versioned CSV schema definitions + examples
- Model-first architecture aligned to Lex constraints
- Approved UML + ER diagrams
- Approved implementation-grade pseudocode
- Final planning-wide rule compliance validation against [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]]

## Session templates

- [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]]
- [[templates/copilot-session-template|Copilot Session Template]]
- [[templates/hitl-step-template|HITL Step Template]]
- [[templates/llm-response-structure|LLM Response Structure (Mandatory)]]

## Non-negotiable rules for Copilot

- Ask only for missing information.
- Keep decisions explicit and logged.
- Prioritize data model clarity over early algorithm detail.
- Treat Django ORM project/domain models as the system backbone.
- Treat `LexModel` and `CalculationModel` as preimplemented framework internals (use/subclass only; do not re-implement).
- Treat [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]] as authoritative for all Lex-specific decisions.
- Gate progression with explicit `Approved: Yes/No` per step.
