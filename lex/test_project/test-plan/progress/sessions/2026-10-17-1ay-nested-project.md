---
date: 2026-10-17
clusters: [1]
tests_added: "7 new scenarios (1.402-1.408)"
suite_tally: "1ay 7 pass; init cluster 636 pass / 14 skip"
---

# A Lex project nested in a repository runs without extra variables

On feat/nested-project-root. The CLI took the git toplevel as the project root, so a Lex project in a monorepo needed GIT_CEILING_DIRECTORIES, PYTHONPATH and DJANGO_SETTINGS_MODULE from a wrapper. The nearest folder with a lex_config.py is now the root, and a settings.py there is the settings module for the CLI, the Reflex bootstrap and their child processes.

Batch: [1ay](../../clusters/01-init/batches.md).
