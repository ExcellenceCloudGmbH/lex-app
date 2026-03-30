# Patterns, Pitfalls, and Checklists

Search keywords: recipes, anti-patterns, gotchas, model checklist, implementation checklist

## Scope

- Reusable coding patterns
- Common failure modes and anti-patterns
- Build checklists for Lex model classes

## Key Points

- Prefer idempotent calculation/upload patterns.
- Keep heavy compute logic isolated from save orchestration hooks.
- Follow model-type checklists (`LexModel`, `CalculationModel`, ingestion, combinatorial mixins).
- Avoid bypassing framework conventions for permissions, hooks, and registration.

## Where to Expand

- `lex_context.md`: Complete Model Creation Checklist; Common Patterns & Recipes; Anti-Patterns & Pitfalls
- `lex_context_repo.md`: Common Patterns & Conventions

## LLM Prompt Starters

- "Use the Lex checklist to review this model implementation and list missing items only."
- "Identify likely anti-patterns in this calculation/upload code and propose minimal fixes."
