---
date: 2026-08-12
clusters: [1]
tests_added: 34
suite_tally: "1ab: 34 pass / 0 fail"
---

# Batch 1ab — MCP tools infrastructure

Regression coverage for [PR #703](https://github.com/ExcellenceCloudGmbH/lex-app/pull/703)
(`fix/mcp-server-fastmcp4-port`), which modified four framework source files without
paired cluster tests. The batch covers all four files across twelve test classes in
[`test_1ab_mcp_tools_infrastructure.py`](../../../../test_project/tests/init/test_1ab_mcp_tools_infrastructure.py).
See the full batch record in [`clusters/01-init/batches.md`](../../clusters/01-init/batches.md).

**embed.py (`lex/mcp_server/tools/embed.py`)** — the MCP Apps embed tool derives a
view-type label from URL path segments (`_classify_path`), builds a human-readable
title (`_build_title`), and resolves the frontend base URL from a priority chain
(`_resolve_frontend_url`). Tests pin all eight path-classification patterns, three title
variants, three URL-resolution priorities, and the CSP origin list contract.

**mcp_mode_invoke.py** — the out-of-process mode-switch invoker validates the target
mode, records structured results in `InvokeSwitchResult` (including the `ok` sentinel),
and falls back to local lex-app helpers when `lex_mcp.mode_switch` is unavailable. The
fallback path is exercised with `sys.modules` patching so no live MCP server is needed.

**setup_with_ai.py** — `normalize_mcp_mode` accepts any recognised mode string and
defaults unknown values to `"forward"`. `normalize_ai_environments` parses
comma-separated names and falls back to the default environment. `update_env_file`
adds new keys and updates existing ones in place without duplicating lines.

**verify_ai_assets.py** — `resolve_active_mcp_mode` follows a six-level precedence
chain (`explicit_mode` → override file → project `.env` → `mcp.json` → process env →
default). Four scenarios cover the top three levels and the error path for unknown modes.
`verify_directory` restores missing files, overwrites drifted files, and skips cleanly
when the source directory is absent.
