---
tags: [template, kickoff, user-prompt, planning]
---

# Initial User Prompt (Copy/Paste to Start)

Use this as your very first message to Copilot when starting a new project planning run.

## Prompt

```md
You are my planning copilot for this repository.

Use `HANDBOOK_ROOT=./.venv/lib/python3.12/site-packages/lex/docs` as the canonical handbook location and execute Steps 0–8 in order.

Hard rules:
1. Treat `${HANDBOOK_ROOT}` as read-only handbook content.
2. For tool/runtime dependencies, assume only `${HANDBOOK_ROOT}` and the connected `lex-mcp` server are required.
3. Retrieve planning step instructions only via MCP using `get_planning_step`.
4. Execute one planning step at a time: one MCP request for one step, complete it, then request the next step.
5. Write all generated artifacts only under `plans/<run-id>/`.
6. Start by creating a run folder using today’s date and a project slug.
7. Copy `${HANDBOOK_ROOT}/runs/run-template.md` to `plans/<run-id>/run.md`.
8. Begin by requesting planning Step 0 from MCP and do not skip steps.
9. At the end of each step, use the exact end block from `${HANDBOOK_ROOT}/planning/templates/llm-response-structure.md`.
10. Do not proceed to the next step unless the current step has explicit approval when required.
11. Apply `${HANDBOOK_ROOT}/lex_topics/20-LEX-SPECIFICATIONS.md` as canonical Lex ground truth.
12. Lex is an implementation framework contract, not a standalone traceability requirement policy.
13. This project is CSV-first; do not introduce non-CSV I/O assumptions.
14. Tests/runtime orchestration/reverse-analysis are out of scope unless explicitly requested by the user.
15. Enforce Lex folder architecture: `Inputs` for transformed data, `Uploads` for file-ingestion models, `Reports` for report-generation models.
16. Ensure report models are planned with at least one Django `FileField`.
17. Do not plan or generate Django project bootstrap/scaffold artifacts (`apps.py`, `urls.py`, `settings.py`, etc.) unless explicitly requested.
18. Step 8 must validate all planning outputs against `${HANDBOOK_ROOT}/lex_topics/20-LEX-SPECIFICATIONS.md` before implementation can start.

Project seed inputs:
- Project name: <fill>
- Business goal: <fill>
- Users/stakeholders: <fill>
- Constraints or compliance notes: <fill>

Now do the following in order:
- Propose the run-id and path.
- Confirm which file you will create/update first.
- Start Step 0 with concise intake questions.
```

## Expected first LLM actions

- Propose `plans/YYYY-MM-DD-<project-slug>/`.
- Create/update `plans/<run-id>/run.md`.
- Request and execute planning Step 0 via `get_planning_step`.
- End response with required next-action/self-notes block.
