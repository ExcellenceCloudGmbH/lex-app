---
date: 2026-08-13
clusters: [1]
tests_added: 8
suite_tally: "1ab 8 pass / 0 fail"
---

# AI setup / verify compatibility shims + standalone FastMCP embed registration

Completed **Batch 1ab** to cover the PR #703 AI/MCP tooling refactor: the
`lex setup-with-ai` cold-start surface, the legacy `lex.tools.mcp_mode_invoke`
and `lex.tools.verify_ai_assets` shim imports, and the standalone FastMCP
registration seam in `lex_embed_view`.

The sandbox has no PostgreSQL service, so the documented `python -m lex pytest`
path stops during Django's test-database bootstrap. This batch is pure unit
coverage and passes via direct pytest instead. See
[the Batch 1ab record](../../clusters/01-init/batches.md).
