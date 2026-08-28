---
date: 2026-08-13
clusters: [1]
tests_added: 5
suite_tally: "1ab: 5 pass / 0 fail"
---

# Batch 1ab — MCP embed + AI setup/verify tooling contracts

Coverage batch for PR #703 targeting MCP/AI setup and verification surfaces.
Details are recorded in [cluster 01 init batches](../../clusters/01-init/batches.md).

This batch adds regression tests for:

- embed URL construction and token redaction behaviour in `lex_embed_view`,
- unsupported mode rejection in mode switching,
- `.env`-driven runtime mode alignment in `ai-verify`,
- exact `project_root` targeting in `configure_ai_integration`.
