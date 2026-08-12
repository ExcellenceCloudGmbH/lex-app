---
date: 2026-08-12
clusters: [init]
tests_added: 41
suite_tally: "41 pass / 0 fail"
---

Coverage task for PR #703 (issue #710): four framework source files under
`lex/mcp_server/tools/embed.py`, `lex/tools/mcp_mode_invoke.py`,
`lex/tools/verify_ai_assets.py`, and `lex/tools/setup_with_ai.py` were shipped
without paired tests.

Batch [1ab](../../../test-plan/clusters/01-init/batches.md#batch-1ab) covers
the full observable-behaviour surface of these modules:

- **embed.py** — `_resolve_frontend_url` priority chain (MCP setting → REACT_APP_URL → LEX_FRONTEND_URL → fallback), `_classify_path` view-type detection (list / create / detail / edit / custom), `_build_embed_url` param injection and fragment guarantee, `_build_title` resource formatting.  Tests inject lightweight `sys.modules` stubs for the `mcp` SDK, which is absent from the CI environment.
- **mcp_mode_invoke.py** — `_normalise_mode` rejects unknown modes and strips whitespace, `InvokeSwitchResult.ok` reflects errors, `invoke_switch_to_mode` completes without crashing when neither `lex_mcp` nor fallback helpers are available (noop strategy with `stop_server=False`).
- **verify_ai_assets.py** — `_read_env_file_value` parses plain, commented-out, and quoted lines, `resolve_active_mcp_mode` respects the documented precedence (explicit > override > project .env > default), `verify_directory` skips on None source, restores missing files, and is ok when destination matches, `VerifyAIAssetsResult.ok` aggregates directory outcomes, `_read_mode_from_mcp_json` extracts `--mode` arg from a lex-mcp-local entry and ignores unrelated servers.
- **setup_with_ai.py** — `LEX_MCP_LOCAL_SERVER_NAME` constant is `'lex-mcp-local'`, `LEX_APP_EMBEDDED_DIRECTORY_NAMES` includes `'docs'`, `update_env_file` inserts new keys and replaces existing ones.

All 41 tests pass.  Closes #710.
