"""Regression guards for MCP mode alignment and embed-view token handling.

Intent: the AI bootstrap/verification flow treats the project's ``.env`` mode
as the source of truth, can realign a drifted MCP runtime before verification,
and emits embed links that keep iframe bootstrap tokens out of model-visible
narration text.

Cluster 1ab — scenarios 1.223–1.227. Type: U.
Covers: ``lex/tools/verify_ai_assets.py``, ``lex/tools/mcp_mode_invoke.py``,
``lex/tools/setup_with_ai.py``, ``lex/mcp_server/tools/embed.py``.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_ai_mode_and_embed_contracts.py -v
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pytest

from lex.tools import mcp_mode_invoke, setup_with_ai, verify_ai_assets

pytestmark = pytest.mark.init


class TestCluster01ab_AiModeAndEmbedContracts(unittest.TestCase):
    """Cluster 1ab: mode realignment and embed token-handling contracts."""

    def test_1_223_verify_align_invokes_mode_switch_for_runtime_drift(self):
        """
        Scenario 1.223: ai-verify realigns runtime mode when it drifts from project .env.
        Given: project ``.env`` declares forward mode while mcp.json still says backward
        When: verify_ai_assets runs with mode alignment enabled
        Then: it invokes the switch-to-mode primitive for the .env mode before verification
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            env_path.write_text('LEX_MCP_MODE="forward"\n', encoding="utf-8")
            mcp_path.write_text(
                json.dumps(
                    {
                        "servers": {
                            "lex-mcp-local": {
                                "args": ["-m", "lex_mcp.server", "--mode", "backward"],
                                "env": {"LEX_MCP_MODE": "backward"},
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )

            with patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode") as invoke_mock, patch.object(
                verify_ai_assets, "resolve_active_mcp_mode", return_value=("forward", "project-dotenv")
            ), patch.object(
                verify_ai_assets, "resolve_active_python_executable", return_value=Path("python")
            ), patch.object(
                verify_ai_assets, "_resolve_package_directory", return_value=None
            ), patch.object(
                verify_ai_assets, "resolve_lex_app_package_root", return_value=None
            ):
                result = verify_ai_assets.verify_ai_assets(
                    project_root=root,
                    mode=None,
                    align_mcp_mode=True,
                    mcp_config_path=mcp_path,
                    sync_environments=False,
                )

            invoke_mock.assert_called_once()
            self.assertEqual(
                invoke_mock.call_args.args[0],
                "forward",
                "Mode alignment must switch runtime to the project's .env mode.",
            )
            self.assertEqual(result.mode, "forward")

    def test_1_224_verify_all_mode_skips_runtime_alignment(self):
        """
        Scenario 1.224: ``--mode all`` verifies assets without switching runtime mode.
        Given: alignment is enabled explicitly
        When: verify_ai_assets is asked to verify every mode
        Then: no switch-to-mode invocation occurs because no single runtime mode is targeted
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")

            with patch("lex.tools.mcp_mode_invoke.invoke_switch_to_mode") as invoke_mock, patch.object(
                verify_ai_assets, "resolve_active_python_executable", return_value=Path("python")
            ), patch.object(
                verify_ai_assets, "_resolve_package_directory", return_value=None
            ), patch.object(
                verify_ai_assets, "resolve_lex_app_package_root", return_value=None
            ):
                verify_ai_assets.verify_ai_assets(
                    project_root=root,
                    mode="all",
                    align_mcp_mode=True,
                    sync_environments=False,
                )

            invoke_mock.assert_not_called()

    def test_1_225_invoke_switch_rejects_unknown_mode(self):
        """
        Scenario 1.225: invalid modes fail fast with a clear error.
        Given: a mode value outside the supported MCP set
        When: invoke_switch_to_mode is called
        Then: it raises ValueError before any sync/write side effects run
        """
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                mcp_mode_invoke.invoke_switch_to_mode(
                    "banana",
                    project_root=Path(tmp),
                    mcp_config_path=Path(tmp) / "mcp.json",
                )

    def test_1_226_read_dotenv_value_unquotes_and_ignores_comments(self):
        """
        Scenario 1.226: dotenv reads are stable for quoted values and commented lines.
        Given: a .env file with comments and a quoted LEX_MCP_MODE assignment
        When: _read_dotenv_value reads the key
        Then: it returns the unquoted value and ignores non-assignment lines
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text(
                "# comment\nLEX_MCP_MODE='review'\nOTHER_KEY=value\n",
                encoding="utf-8",
            )

            self.assertEqual(setup_with_ai._read_dotenv_value(env_path, "LEX_MCP_MODE"), "review")
            self.assertIsNone(setup_with_ai._read_dotenv_value(env_path, "MISSING"))

    def test_1_227_embed_narration_omits_auth_token_but_structured_url_keeps_it(self):
        """
        Scenario 1.227: embed tool keeps iframe auth token out of model-visible narration.
        Given: the MCP principal carries an access token used for iframe bootstrap
        When: lex_embed_view builds narration + structured payload
        Then: structured embed_url includes auth_token, but narration uses a clean URL
        """
        principal = SimpleNamespace(access_token="secret-token")
        config_module = types.ModuleType("lex.mcp_server.config")
        config_module.mcp_setting = lambda _key: None
        registry_module = types.ModuleType("lex.mcp_server.registry")
        registry_module.container_is_writable = lambda _resource: False
        registry_module.get_container = lambda _resource: None
        context_module = types.ModuleType("lex.mcp_server.context")
        context_module.current_principal = lambda: principal
        fastmcp_module = types.ModuleType("mcp.server.fastmcp")
        fastmcp_module.FastMCP = object
        fastmcp_resources_module = types.ModuleType("mcp.server.fastmcp.resources")
        fastmcp_resources_module.FunctionResource = object
        mcp_types_module = types.ModuleType("mcp.types")

        class _TextContent:
            def __init__(self, type: str, text: str):
                self.type = type
                self.text = text

        class _ToolAnnotations:
            def __init__(self, **_kwargs):
                pass

        mcp_types_module.TextContent = _TextContent
        mcp_types_module.ToolAnnotations = _ToolAnnotations

        with patch.dict(
            "sys.modules",
            {
                "lex.mcp_server.config": config_module,
                "lex.mcp_server.registry": registry_module,
                "lex.mcp_server.context": context_module,
                "mcp.server.fastmcp": fastmcp_module,
                "mcp.server.fastmcp.resources": fastmcp_resources_module,
                "mcp.types": mcp_types_module,
            },
        ):
            sys.modules.pop("lex.mcp_server.tools.embed", None)
            embed = importlib.import_module("lex.mcp_server.tools.embed")
            with patch.object(embed, "_resolve_frontend_url", return_value="https://frontend.example"):
                blocks = asyncio.run(embed._embed_view_inner(path="process-history"))

        narration_text = blocks[0].text
        structured = json.loads(blocks[1].text)
        embed_url = structured["embed_url"]

        self.assertIn("auth_token=secret-token", embed_url)
        self.assertNotIn(
            "auth_token=secret-token",
            narration_text,
            "Model-visible narration must not expose the iframe bootstrap token.",
        )
