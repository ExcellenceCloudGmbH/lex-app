---
date: 2026-08-13
clusters: [1]
tests_added: 5
suite_tally: "1ab: 5 pass / 0 fail"
---

# Batch 1ab — MCP mode alignment + embed token hygiene regressions

Added [batch 1ab](../../clusters/01-init/batches.md) to cover the PR #703 AI
runtime-mode alignment + embed-token surfaces in init tooling, with focused
unit-level guards for:

- runtime drift realignment via `verify_ai_assets(..., align_mcp_mode=True)`,
- `mode=all` no-switch behavior,
- invalid mode rejection in `invoke_switch_to_mode`,
- dotenv quote/comment parsing stability in setup helpers,
- model-visible narration never echoing iframe `auth_token` values.
