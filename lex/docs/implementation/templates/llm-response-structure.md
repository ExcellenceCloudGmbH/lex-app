---
tags: [template, response, protocol, hitl, implementation]
---

# LLM Response Structure (Mandatory)

Use this exact end-of-response block in every implementation step response.

## Response body order

1. Step output for current step
2. Validation summary (ambiguities, risks, conflicts)
3. Traceability updates (Requirement IDs, Story IDs, code/test references)
4. Approval status (`Approved: Yes/No/Pending`)

## End-of-response block (required)

```md
## Next User Action
- <1-3 precise things the user should provide or approve next>

## Next Step Pointer
- Next handbook step: <docs/implementation/NN-...>
- File to create/update now: <plans/<run-id>/implementation/step-NN-...md or run.md>

## LLM Self-Notes (Next Turn)
- <what the LLM must do in the next response>
- <which file in plans/implementation it must update>
- <what must be validated before moving to next step>
```

## Constraints

- Keep this block at the end of every response.
- Never include private chain-of-thought; only actionable execution notes.
- If waiting for user approval, state exactly what is blocked.
- Always provide both a handbook-step pointer and a concrete `plans/` file pointer.
