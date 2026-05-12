---
description: "Use when: the user is working on a Lex project, implementing a step, writing code for a Lex app, or any time the ./docs/ folder is relevant. Reminds the LLM to consult the authoritative framework docs before implementing."
---

# Lex Framework Docs — Read Before Implementing

The project working directory contains a `./docs/` folder with the **authoritative framework rules and conventions**.

## Rules

1. **Read before implementing.** Before writing any code for a step, read the relevant files in `./docs/`. These docs describe the framework's patterns, naming conventions, file structure, and constraints.
2. **Docs override prior knowledge.** If your training data conflicts with what the docs say, the docs win. Always follow the docs.
3. **Reference specific docs.** When the step instructions mention a convention or pattern, find the corresponding doc file and read it before proceeding.
4. **Do not guess framework APIs.** If you're unsure about a framework API, pattern, or convention, check the docs first. Do not rely on general knowledge.

## Common doc files to check

- Look for files describing project structure, naming conventions, component patterns, and deployment rules.
- Step 0 (`get_plan_step(step=0)`) provides an overview of all available docs — start there if unsure which file to read.
