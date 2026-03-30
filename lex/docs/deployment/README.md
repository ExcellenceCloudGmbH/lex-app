---
tags: [deployment, lex, copilot]
---

# Deployment Phase (Step 0-2)

This phase deploys the implemented Lex application — installs dependencies, configures the environment, initializes the database and Keycloak, starts the server, and validates the deployment.

## Start here (single entry point)

1. Open and copy: [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]].
2. Paste it as your first user message for deployment.
3. Let Copilot create `plans/<run-id>/deployment/run.md` and begin Step 0.
4. Apply [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]].

## Input contract

- Deployment starts only after implementation Step 11 is approved.
- Read planning artifacts from `plans/<run-id>/step-00..step-08*.md`.
- Read implementation artifacts from `plans/<run-id>/implementation/step-00..step-11*.md`.
- Treat implementation outputs as the source of truth unless user explicitly revises requirements.

## Output destination rule

- `docs/` files are handbook-only and must not be edited during runs.
- All deployment outputs must be created in `plans/<run-id>/deployment/`.

## MCP step retrieval contract

- Do not read deployment step instructions from local files during execution.
- Retrieve each step from MCP using `get_deployment_step`.
- Request and complete exactly one step at a time.
- After finishing Step `N`, make a new MCP request for Step `N+1`.
- Deployment starts only after implementation is fully complete and approved.

## Step order

0. [[00-setup-and-initialization|Setup and Initialization]]
1. [[01-start-and-verify|Start and Verify the Application]]
2. [[02-deployment-compliance-validation|Deployment Compliance Validation (Final Gate)]]

## Step routing map (where to go next)

- After Step 0 output: update `plans/<run-id>/deployment/step-00-setup.md`, then continue to Step 1.
- After Step 1 output: update `plans/<run-id>/deployment/step-01-start-verify.md`, then continue to Step 2.
- After Step 2 output: update `plans/<run-id>/deployment/step-02-compliance.md`; require explicit final deployment approval.

## Mandatory deployment outputs

- Verified environment configuration (`.env` populated, Keycloak synced)
- Running application with confirmed API access
- Final deployment compliance validation against [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]]

## Session templates

- [[templates/initial-user-prompt|Initial User Prompt (Copy/Paste to Start)]]
- [[templates/copilot-session-template|Copilot Session Template]]
- [[templates/hitl-step-template|HITL Step Template]]
- [[templates/llm-response-structure|LLM Response Structure (Mandatory)]]

## Non-negotiable rules for Copilot

- Do not deploy without explicit `Approved: Yes` checkpoint.
- Keep traceability from planning + implementation outputs into deployment artifacts.
- Treat [[../lex_topics/20-LEX-SPECIFICATIONS|Lex Specifications (Canonical, Project-Specific)]] as authoritative for all Lex-specific decisions.
- Gate progression with explicit `Approved: Yes/No` at required checkpoints.
