---
date: 2026-07-23
clusters: [1]
tests_added: 8
suite_tally: "1y 8 pass / 0 fail / 10 subtests; setup regression 13 pass / 0 fail / 9 subtests"
---

# IDE-aware setup run configurations

Completed **Batch 1y** so `lex setup`, `lex setup-with-ai`, and the standalone
configuration generator select VS Code or PyCharm from reliable process markers
and generate both formats whenever detection is absent or conflicting.

VS Code receives ten `debugpy` launch entries with command, argument,
environment, working-directory, `.env`, and Celery worker-prompt parity. The
generator merges its namespaced entries into existing JSON/JSONC and is
content-idempotent. See
[the Batch 1y record](../../clusters/01-init/batches.md).
