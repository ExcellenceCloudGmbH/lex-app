---
tags: [template, response, protocol, hitl]
---

# LLM Response Structure (Mandatory)

Use this exact end-of-response block in every planning step response.

## Response body order

1. Step output for current step (content requested by handbook)
2. Validation summary (ambiguities, risks, conflicts)
3. Traceability updates (Requirement IDs, Story IDs, Model references)
4. Completion status (`Completed: Yes/No` and remaining blockers if `No`)

## End-of-response block (required)

```md
## Next User Action
- <1-3 precise things the user should provide next, only if required>

## Next Step Pointer
- Next handbook step: <docs/planning/NN-...>
- File to create/update now: <plans/<run-id>/step-NN-...md or run.md>

## LLM Self-Notes (Next Turn)
- <what the LLM must do in the next response>
- <which file in plans/ it must update>
- <what must be validated before moving to next step>
```

## Constraints

- Keep this block at the end of every response.
- Never include private chain-of-thought; only actionable execution notes.
- If blocked, state exactly what is missing and why the next step cannot proceed.
- Always provide both a handbook-step pointer and a concrete `plans/` file pointer.
