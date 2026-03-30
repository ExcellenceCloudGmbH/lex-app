---
tags: [template, kickoff, user-prompt, implementation]
---

# Initial User Prompt (Copy/Paste to Start)

Use this as your first message to Copilot when starting implementation from an approved planning run.

## Prompt

```md
You are my implementation copilot for this repository.

Use `HANDBOOK_ROOT=./.venv/lib/python3.12/site-packages/lex/docs` as the canonical handbook location and execute Steps 0–11 in order.

Hard rules:
1. Treat `${HANDBOOK_ROOT}` as read-only handbook content.
2. For tool/runtime dependencies, assume only `${HANDBOOK_ROOT}` and the connected `lex-mcp` server are required.
3. Start implementation only after planning is fully complete and approved.
4. Retrieve implementation step instructions only via MCP using `get_implementation_step`.
5. Execute one implementation step at a time: one MCP request for one step, complete it, then request the next step.
6. Write all generated implementation artifacts only under `plans/<run-id>/implementation/`.
7. Read planning artifacts from `plans/<run-id>/step-00..step-08*.md` as source-of-truth inputs.
8. Begin by requesting implementation Step 0 from MCP and do not skip steps.
9. At the end of each step, use the exact end block from `${HANDBOOK_ROOT}/implementation/templates/llm-response-structure.md`.
10. Do not proceed past gated approvals.
11. Treat `LexModel` and `CalculationModel` as preimplemented framework internals (use/subclass only; never re-implement).
12. Apply `${HANDBOOK_ROOT}/lex_topics/20-LEX-SPECIFICATIONS.md` as canonical Lex ground truth.
13. Use CSV-only input/output assumptions for this project unless explicitly overridden.
14. Step 10 must deliver complete project code for approved scope (not implementation planning only).
15. Enforce Lex folder architecture: `Inputs`, `Uploads`, `Reports`.
16. Ensure each report model defines at least one Django `FileField`.
17. Do not generate Django scaffold/bootstrap files (`apps.py`, `urls.py`, `settings.py`, etc.) unless explicitly requested by the user.
18. Step 9 must validate implementation plans against `${HANDBOOK_ROOT}/lex_topics/20-LEX-SPECIFICATIONS.md` before Step 10 coding starts.
19. Step 11 must validate generated code against `${HANDBOOK_ROOT}/lex_topics/20-LEX-SPECIFICATIONS.md` before final deployment approval.
20. Use `${HANDBOOK_ROOT}/lex_topics/21-LEX-APP-CONTEXT.yaml` when framework internals or lifecycle behavior details are needed.

Context for this run:
- Run ID: <fill>
- Planning folder: plans/<run-id>/
- Implementation goal: <fill>
- Constraints: <fill>

Now do the following in order:
- Confirm the run path for implementation outputs.
- Confirm which implementation file you will create/update first.
- Start Step 0 with a scope-lock and traceability kickoff.
```
