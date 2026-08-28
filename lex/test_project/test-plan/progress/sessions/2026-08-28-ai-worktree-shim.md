---
date: 2026-08-28
clusters: [01-init]
tests_added: 6
suite_tally: "6 pass / 0 fail"
---

# Session: ai_worktree shim coverage (PR #730)

Coverage task for PR ExcellenceCloudGmbH/lex-app#729 (`feat/ai-command-passthrough`).

Batch **1ab** (scenarios 1.223–1.226) — see
[`clusters/01-init/batches.md`](../clusters/01-init/batches.md).

Adds `lex/tools/ai_worktree.py` (compatibility shim, re-export from
`lex_mcp.ai_worktree`) and three test classes that cover its observable
behaviours: missing-dependency `ImportError` with recovery hint,
`__getattr__` delegation, and `__dir__` delegation.
