---
date: 2026-09-02
clusters: [1]
tests_added: 7
suite_tally: "1ab 7 pass / 0 fail"
---

# Coverage task: `lex-mcp-local` onboarding shims (PR #733 / issue #736)

Completed **Batch 1ab** covering three of the four framework source files
named in the auto-opened coverage task (issue #736) for PR #733:
`lex/tools/mcp_mode_invoke.py`, `lex/tools/verify_ai_assets.py`, and the
cold-start fallback / mode-picker gating in `lex/tools/setup_with_ai.py`. Both
shim modules now have coverage for their actionable-`ImportError` contract
(naming `lex ai-update` for a stale install and `lex setup-with-ai` for none
at all) and their re-export/`__getattr__`/`__dir__` delegation once
`lex-mcp-local` is present, exercised against a minimal fake `lex_mcp` package
injected through `sys.modules`.

The fourth file, `lex/mcp_server/tools/embed.py`, is not covered by a new
test: it cannot be imported at all in this checkout because
`lex.mcp_server.config` / `lex.mcp_server.registry` do not exist yet (the
rest of `lex/mcp_server` lives on an unmerged branch — see `AGENTS.md`, "One
trap in `lex/mcp_server`"). Its specific regression in PR #733 (the vendored
`mcp.server.fastmcp` import and the FastMCP-1.0 `add_tool(fn, name=...)` call
shape) is already covered generically, without importing the module, by the
existing static `lex/tests/unit/infra/test_mcp_server_sdk_compat.py`. See
[the Batch 1ab record](../../clusters/01-init/batches.md).
