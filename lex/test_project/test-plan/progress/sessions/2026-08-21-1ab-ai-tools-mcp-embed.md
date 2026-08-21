---
date: 2026-08-21
clusters: [1]
tests_added: 26
suite_tally: "26 pass / 0 fail"
---

Batch **1ab** — AI tooling shims + MCP embed URL helpers.

Adds `test_1ab_ai_tools_and_mcp_embed.py` covering four source files that
shipped without test coverage in PR #726 (`mbertolino/lex-5-client-admin-role`):

- `lex/tools/setup_with_ai.py` — pure-function contract: `DEFAULT_LEX_MCP_MODE`
  is `"brief"`, `SUPPORTED_MCP_MODES` always contains it, `_installed_mode_roster`
  returns `None` on a cold start, `normalize_mcp_mode` accepts valid modes and
  falls back for unknown/None, `resolve_submitted_mcp_mode` enforces the
  `MODE_OVERRIDE_FIELD` acknowledgement gate, and `_resolve_environment_alias`
  maps known IDE names.

- `lex/tools/mcp_mode_invoke.py` — shim re-exports `InvokeSwitchResult` /
  `invoke_switch_to_mode`, derives `SUPPORTED_MCP_MODES` from `MODE_TO_PACKAGE`,
  and delegates `__getattr__` / `__dir__` to the `lex_mcp.mode_switch` impl.

- `lex/tools/verify_ai_assets.py` — shim `__getattr__` / `__dir__` delegation
  to `lex_mcp.ai_assets`.

- `lex/mcp_server/tools/embed.py` — `_classify_path` (list / create / detail /
  edit), `_build_title` (with + without container), `_resolve_frontend_url`
  (env-var priority + localhost fallback), `_build_embed_url` (always injects
  `embed=true`).

All 26 tests pass. `lex_mcp` and `fastmcp` are absent in the test environment
and are injected as `sys.modules` stubs; `embed.py` is loaded via
`importlib.util.spec_from_file_location` because its parent directories lack
`__init__.py`.

See [`clusters/01-init/batches.md`](../../clusters/01-init/batches.md) — batch 1ab.
