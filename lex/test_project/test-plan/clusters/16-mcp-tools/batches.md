## Batch 16a — embed-view URL building and path classification

- **Scenarios:** 16.1–16.27
- **Type:** U (unit)
- **Files covered:** `lex/mcp_server/tools/embed.py`
- **Test file:** `lex/test_project/tests/mcp_tools/test_16a_embed.py`
- **Test class:** `TestCluster16a_ClassifyPath`, `TestCluster16a_BuildTitle`,
  `TestCluster16a_ResolveFrontendUrl`, `TestCluster16a_CspOrigins`,
  `TestCluster16a_BuildEmbedUrl`
- **Status:** complete — 27 pass / 0 fail / 0 skip

## Batch 16b — MCP mode resolution and asset verification

- **Scenarios:** 16.28–16.45
- **Type:** U (unit)
- **Files covered:** `lex/tools/verify_ai_assets.py`
- **Test file:** `lex/test_project/tests/mcp_tools/test_16b_verify_assets.py`
- **Test class:** `TestCluster16b_ReadEnvFileValue`, `TestCluster16b_ResolveActiveMcpMode`,
  `TestCluster16b_VerifyDirectory`
- **Status:** complete — 18 pass / 0 fail / 0 skip

## Batch 16c — MCP mode-switch invocation from outside the server

- **Scenarios:** 16.46–16.57
- **Type:** U (unit)
- **Files covered:** `lex/tools/mcp_mode_invoke.py`
- **Test file:** `lex/test_project/tests/mcp_tools/test_16c_mcp_mode_invoke.py`
- **Test class:** `TestCluster16c_NormaliseMode`, `TestCluster16c_InvokeSwitchResultOk`,
  `TestCluster16c_InvokeSwitchToMode`
- **Status:** complete — 12 pass / 0 fail / 0 skip

## Batch 16d — .env file management

- **Scenarios:** 16.58–16.65
- **Type:** U (unit)
- **Files covered:** `lex/tools/setup_with_ai.py`
- **Test file:** `lex/test_project/tests/mcp_tools/test_16d_update_env_file.py`
- **Test class:** `TestCluster16d_UpdateEnvFile`
- **Status:** complete — 8 pass / 0 fail / 0 skip
