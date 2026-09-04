## 16. AI Tooling (MCP embed tool, `setup-with-ai`, MCP shims)

**Opened 2026-09-02** as a coverage-task response (issue #734, parent PR #733) for
four framework source files that shipped without paired tests when
`lex/mcp_server/`, the AI-setup form, and the MCP compatibility shims were
introduced: `lex/mcp_server/tools/embed.py`, `lex/tools/setup_with_ai.py`,
`lex/tools/mcp_mode_invoke.py`, `lex/tools/verify_ai_assets.py`. No existing
cluster owns MCP-server or AI-onboarding-tool surfaces, so this is a new
cluster rather than an extension.

**What it tests:** The MCP `lex_embed_view` tool's frontend-URL resolution,
CSP origin list, path→view-type classification, and full async tool
behaviour (custom routes, container metadata, OIDC token injection); the
`lex setup-with-ai` browser form's MCP-mode override gate and AI-environment
alias/`all`/strict normalization, plus its `.env` file read/update/atomic-write
persistence; and the two `lex.tools.*` modules that are pure compatibility
shims over the separate `lex-mcp-local` package (`lex_mcp`), covering both
the "package absent" `ImportError`-with-recovery-hint path (exercised for
real — `lex_mcp` is genuinely not installed here) and the "package present"
re-export / `__getattr__` / `__dir__` delegation path (exercised against a
minimal fake `lex_mcp`).

**Scenario table:**

| # | File | Scenarios | Purpose |
|---|------|-----------|---------|
| 16a | `test_16a_embed_url_builder.py` | 16.1 – 16.28 | `embed.py` frontend URL resolution/CSP origins, path→view-type classification, title building, embed URL query/fragment construction, and the `_embed_view` async MCP tool entry point end-to-end (custom routes, container metadata attach/degrade-on-error, access-token injection that must not leak into the model-visible narration). |
| 16b | `test_16b_setup_with_ai_mode_and_env.py` | 16.29 – 16.43 | `setup_with_ai.py` MCP-mode acknowledgement gate (`resolve_submitted_mcp_mode`) and AI-environment alias/`all`/dedup normalization (`normalize_ai_environments`), including the strict-raises-`SetupWithAIError` vs `strict=False`-degrades-to-default contract. |
| 16c | `test_16c_setup_with_ai_env_persistence.py` | 16.44 – 16.54 | `setup_with_ai.py` `.env` persistence: safe-vs-JSON-quoted value encoding, in-place key replacement without duplicating lines, comment/blank-line preservation, legacy `COPILOT_GITHUB_TOKEN` retirement, and atomic-write semantics. |
| 16d | `test_16d_mcp_shim_compat.py` | 16.55 – 16.61 | `mcp_mode_invoke.py` / `verify_ai_assets.py` compatibility-shim contract: helpful `ImportError` naming `lex ai-update`/`lex setup-with-ai` when `lex-mcp-local` is absent, and correct re-export + `__getattr__`/`__dir__` delegation (including a derived `SUPPORTED_MCP_MODES`) once it is present. |

**Stub helper:** `lex/test_project/tests/ai_tooling/_mcp_server_stubs.py`
installs minimal fakes for `lex.mcp_server.config` / `.registry` / `.context`
— the three sibling modules `embed.py` imports that do not exist yet in this
checkout (most of `lex/mcp_server` lives on an unmerged branch; see
`lex/tests/unit/infra/test_mcp_server_sdk_compat.py` and AGENTS.md). Nothing
about `embed.py`'s own logic is mocked.
