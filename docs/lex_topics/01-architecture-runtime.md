# Lex Architecture & Runtime

Search keywords: architecture, request flow, calculation flow, stack, runtime

## Scope

- What Lex is and what it provides
- Runtime architecture and execution paths
- Request lifecycle and calculation lifecycle

## Key Points

- Lex is Django/DRF-based with built-in model registration, API generation, calculation orchestration, and audit/real-time layers.
- Core runtime includes Django + DRF + Channels + Celery + Redis + Postgres + Keycloak + Streamlit.
- Request flow: middleware/auth context → view layer → permission filtering → serializer/model handling → response.
- Calculation flow: set status to in-progress → lifecycle hook triggers execution → async if Celery available, otherwise sync fallback.

## Where to Expand

- `lex_context.md`: Framework Overview; How Lex Runs
- `lex_context_repo.md`: What Is Lex; Architecture Overview; Request Flow; Calculation Flow

## LLM Prompt Starters

- "Use this architecture note to explain Lex request flow and where permission checks happen."
- "Given this runtime stack, list likely failure points for calculation execution and fallback behavior."
