## Cluster 16 — AI Tooling

---

### Batch 16a — MCP embed-view tool ✅

| Property | Value |
| --- | --- |
| Scenario range | 16.1 – 16.28 |
| Type | U |
| Files covered | `lex/mcp_server/tools/embed.py` |
| Test file | `lex/test_project/tests/ai_tooling/test_16a_embed_url_builder.py` |
| Test classes | `TestCluster16a_FrontendUrlResolution`, `TestCluster16b_ViewTypeClassification`, `TestCluster16c_TitleBuilding`, `TestCluster16d_EmbedUrlBuilder`, `TestCluster16e_EmbedViewTool` |
| Fixtures | `lex/test_project/tests/ai_tooling/_mcp_server_stubs.py` — installs fake `lex.mcp_server.config`/`.registry`/`.context` modules so the real `embed.py` imports cleanly (those siblings don't exist yet in this checkout) |
| Est. tests | 28 |
| Coverage gain | first coverage of `lex/mcp_server/tools/embed.py` (0% → exercised) |
| Prereqs | none |
| Status | ✅ Complete — 28 pass / 0 fail |
| Note | Coverage task for issue #734 (parent PR #733). Covers the pure URL/title/classification helpers AND the actual async `_embed_view` MCP tool entry point end-to-end: custom-route override, container-metadata attach when resolvable, graceful degrade (no crash, no metadata) when the registry lookup raises, and the OIDC access-token injection contract — the token must land in the widget-only `embed_url` but never in the model-visible narration text. |

---

### Batch 16b — setup-with-ai mode/environment normalization ✅

| Property | Value |
| --- | --- |
| Scenario range | 16.29 – 16.43 |
| Type | U |
| Files covered | `lex/tools/setup_with_ai.py` (`normalize_mcp_mode`, `resolve_submitted_mcp_mode`, `_resolve_environment_alias`, `normalize_ai_environments`) |
| Test file | `lex/test_project/tests/ai_tooling/test_16b_setup_with_ai_mode_and_env.py` |
| Test classes | `TestCluster16f_McpModeOverrideGate`, `TestCluster16g_AiEnvironmentNormalization` |
| Fixtures | none — pure functions, `lex_mcp` absent so the local-mirror fallback tables are exercised |
| Est. tests | 15 |
| Coverage gain | first coverage of the mode-override gate and environment-alias normalization in `setup_with_ai.py` |
| Prereqs | none |
| Status | ✅ Complete — 15 pass / 0 fail |
| Note | Coverage task for issue #734 (parent PR #733). Pins the security-relevant contract explicitly called out in `resolve_submitted_mcp_mode`'s own docstring: a non-default mode submitted without the acknowledgement field silently reverts to the default rather than being honoured, closing the "disabled-attribute is cosmetic only over loopback HTTP" gap. Also pins `normalize_ai_environments`'s strict-raises vs `strict=False`-degrades split. |

---

### Batch 16c — setup-with-ai `.env` persistence ✅

| Property | Value |
| --- | --- |
| Scenario range | 16.44 – 16.54 |
| Type | U |
| Files covered | `lex/tools/setup_with_ai.py` (`_format_env_value`, `update_env_file`, `_read_dotenv_value`, `_atomic_write_text`) |
| Test file | `lex/test_project/tests/ai_tooling/test_16c_setup_with_ai_env_persistence.py` |
| Test classes | `TestCluster16h_FormatEnvValue`, `TestCluster16i_UpdateEnvFileRoundTrip`, `TestCluster16j_AtomicWrite` |
| Fixtures | `tempfile.TemporaryDirectory` per test (`_TempEnvFileTestCase`) — real filesystem, no DB needed |
| Est. tests | 11 |
| Coverage gain | first coverage of `.env` read/write/atomic-write helpers in `setup_with_ai.py` |
| Prereqs | none |
| Status | ✅ Complete — 11 pass / 0 fail |
| Note | Coverage task for issue #734 (parent PR #733). Pins in-place key replacement (no duplicate lines on re-run), comment/blank-line preservation, the legacy `COPILOT_GITHUB_TOKEN` → `GITHUB_TOKEN` migration (removed only as a side effect of writing the replacement, not on unrelated updates), and that `_atomic_write_text` leaves no temp file behind. |

---

### Batch 16d — MCP compatibility shims ✅

| Property | Value |
| --- | --- |
| Scenario range | 16.55 – 16.61 |
| Type | U |
| Files covered | `lex/tools/mcp_mode_invoke.py`, `lex/tools/verify_ai_assets.py` |
| Test file | `lex/test_project/tests/ai_tooling/test_16d_mcp_shim_compat.py` |
| Test classes | `TestCluster16k_ShimImportErrorWithoutLexMcp`, `TestCluster16l_ShimDelegationWithLexMcpPresent` |
| Fixtures | in-file `_install_fake_lex_mcp` / `_fresh_import` context managers — install/evict a minimal fake `lex_mcp` package + shim module cache per test |
| Est. tests | 7 |
| Coverage gain | first coverage of both compatibility-shim modules |
| Prereqs | none |
| Status | ✅ Complete — 7 pass / 0 fail |
| Note | Coverage task for issue #734 (parent PR #733). `lex_mcp` is genuinely absent from this environment (it's a separate PyPI package, not a `lex-app` dependency — see AGENTS.md), so the "package missing" `ImportError` path runs completely unmocked. The "package present" path is exercised against a minimal fake, pinning that `SUPPORTED_MCP_MODES` is *derived* from `lex_mcp.payload.MODE_TO_PACKAGE` rather than duplicated, and that both `__getattr__` and `__dir__` delegate to the real implementation module. |
