# Project Structure & Auto-Discovery

Search keywords: project structure, app.py, discovery, naming conventions, excluded files

## Scope

- Expected project layout for Lex users
- Model discovery behavior and exclusions
- Naming and placement conventions

## Key Points

- Lex auto-discovers model classes from user project Python files.
- Underscore-prefixed/internal bootstrap files are treated specially (`_structure.py`, `_streamlit_structure.py`, auth settings).
- Discovery excludes framework/bootstrap/build folders and migration/system files.
- Stable naming and file placement reduce registration and serializer wiring errors.

## Where to Expand

- `lex_context.md`: Project Structure & Conventions; Django App Configuration
- `lex_context_repo.md`: Project Structure for Lex Users; File Naming Conventions; Excluded from Auto-Discovery

## LLM Prompt Starters

- "Generate a Lex-ready project tree and explain which files are required vs optional."
- "Check my module layout against auto-discovery rules and list what Lex will ignore."
