---
date: 2026-08-12
clusters: [01-init]
tests_added: 15
suite_tally: "15 pass / 0 fail"
---

Coverage tests for PR #703 (`fix/mcp-server-fastmcp4-port`). Adds batch 1ab covering the four
framework source files introduced by the PR:

- `lex/mcp_server/tools/embed.py` — MCP Apps embed tool URL builder and widget resource
- `lex/tools/mcp_mode_invoke.py` — `invoke_switch_to_mode` out-of-process helper
- `lex/tools/setup_with_ai.py` — `normalize_mcp_mode`, `normalize_ai_environments`, `update_env_file`
- `lex/tools/verify_ai_assets.py` — `resolve_active_mcp_mode`, `verify_directory`, env/override readers

See [Batch 1ab in batches.md](../../clusters/01-init/batches.md) for the full scenario table.
