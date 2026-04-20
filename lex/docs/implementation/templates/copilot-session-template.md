---
tags: [template, copilot, session, implementation]
---

# Copilot Session Template (Implementation)

> **Note:** Implementation uses the same `get_plan_step` tool as planning.
> Steps 9–14 cover implementation work. There is no separate implementation tool.

Paste/adapt this at the start of an implementation session.

## Session Prompt

You are supporting the Lex implementation workflow. Continue from Step 9 onward using `get_plan_step`, ask only for missing data, and auto-advance step-by-step without approval gates.

Rules:
1. Keep outputs deterministic and implementation-ready.
2. Use approved planning artifacts as primary inputs.
3. Track requirement/story IDs and maintain traceability to code/tests.
4. For every step, complete outputs and validations, then continue immediately to the next step.
5. Summarize unresolved risks at the end of each step.
6. Treat `docs/` as handbook-only (read-only during runs).
7. Write all generated artifacts only under `plans/<run-id>/`.
8. Start implementation only after planning is fully complete.
9. Retrieve step instructions from MCP using `get_plan_step` (unified tool, steps 0–14).
10. Execute one step per MCP cycle: load Step `N`, complete Step `N`, call `notify_step_complete(step=N, process='planning')`, then load Step `N+1`.
11. Do not skip steps.
12. Never invent input/output CSV schemas; use approved planning Step 2 artifacts.
13. Treat `LexModel` and `CalculationModel` as preimplemented framework internals; only use/subclass them and never re-implement or expand their internals.
14. Apply `docs/lex_topics/20-LEX-SPECIFICATIONS.md` as authoritative Lex contract.
15. Lex is an implementation platform/framework contract, not a standalone traceability requirement policy.
16. Use CSV-only I/O assumptions unless user explicitly overrides.
17. Enforce folder architecture with `Inputs`, `Uploads`, and `Reports` as distinct functional modules.
18. Ensure every report model includes at least one Django `FileField` (null/blank optionality is allowed).
19. Do not generate Django project bootstrap/scaffold files (`apps.py`, `urls.py`, `settings.py`, etc.) unless the user explicitly requests them.
20. Use `docs/lex_topics/21-LEX-APP-CONTEXT.yaml` whenever framework internals or lifecycle behavior details are needed.

Current run note: `plans/<run-id>/run.md`
Current step: <fill>

## Step Kickoff Prompt

- Objective of this step:
- What planning already completed:
- What is still missing:
- Required output structure for this step:
- What planning already completed:
- What is still missing:
- Required output structure for this step:
