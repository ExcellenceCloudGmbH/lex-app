"""Cluster 1ab: MCP embed + AI setup/verify tooling contracts.

Intent
------
These tests pin operator-visible behaviour across the AI/MCP tooling paths:

- MCP embed responses include an iframe URL with auth bootstrap token, but keep
  model-facing narration token-free.
- Mode-switch invocation rejects unsupported modes early.
- AI asset verification treats the project ``.env`` mode as source-of-truth for
  runtime alignment.
- AI setup writes assets/config to the exact project root provided by the
  caller, not an inferred ancestor.

Scenario range: 1.223–1.227.
Run:
python -m lex pytest lex/test_project/tests/init/test_1ab_ai_mcp_tooling_contracts.py -v
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, mock

import pytest

from lex.tools import mcp_mode_invoke
from lex.tools import setup_with_ai as setup_with_ai_module
from lex.tools import verify_ai_assets as verify_module

pytestmark = pytest.mark.init


@contextlib.contextmanager
def _embed_tool_with_stubs():
    """Yield ``lex.mcp_server.tools.embed`` with minimal MCP/runtime stubs."""

    class _TextContent:
        def __init__(self, type: str, text: str):
            self.type = type
            self.text = text

    class _ToolAnnotations:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    stub_modules = {
        "lex.mcp_server.config": SimpleNamespace(mcp_setting=lambda _key: None),
        "lex.mcp_server.registry": SimpleNamespace(
            container_is_writable=lambda _resource: False,
            get_container=lambda _resource: None,
        ),
        "mcp.server.fastmcp": SimpleNamespace(FastMCP=object),
        "mcp.server.fastmcp.resources": SimpleNamespace(FunctionResource=object),
        "mcp.types": SimpleNamespace(
            TextContent=_TextContent,
            ToolAnnotations=_ToolAnnotations,
        ),
    }
    patcher = mock.patch.dict(sys.modules, stub_modules, clear=False)
    patcher.start()
    module = importlib.import_module("lex.mcp_server.tools.embed")
    try:
        yield importlib.reload(module)
    finally:
        patcher.stop()
        try:
            importlib.reload(module)
        except Exception:
            pass


def _run_coroutine_safely(coro):
    """Run a coroutine from sync tests, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list = []
    error: list[BaseException] = []

    def _runner():
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:  # pragma: no cover - defensive
            error.append(exc)

    import threading

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join(timeout=5)
    if thread.is_alive():
        raise RuntimeError("Timed out waiting for embed coroutine to finish.")
    if error:
        raise error[0]
    if not result:
        raise RuntimeError("Embed coroutine finished without returning a result.")
    return result[0]


class TestCluster01ab_McpEmbedContract(TestCase):
    def test_01_223_embed_url_builder_preserves_query_and_sets_embed_flags(self):
        """Scenario 1.223: embed URLs preserve caller params and enforce embed mode.

        Given: A resource path with an existing query parameter.
        When: The embed URL helper builds the iframe URL with hide-toolbar flags.
        Then: Existing query params remain, embed flags are present, and the
        URL fragment advertises embed mode.
        """
        with _embed_tool_with_stubs() as embed_tool:
            url = embed_tool._build_embed_url(
                "quarter/42?existing=1",
                hide_toolbar=True,
                hide_actions=True,
                extra_params={"filter": "open"},
            )

        self.assertIn("existing=1", url, "Caller query parameters must be preserved.")
        self.assertIn("embed=true", url, "Embed mode flag must always be present.")
        self.assertIn("hide_toolbar=true", url, "Toolbar hide flag must propagate.")
        self.assertIn("hide_actions=true", url, "Actions hide flag must propagate.")
        self.assertIn("filter=open", url, "Extra params must be included.")
        self.assertTrue(url.endswith("#embed"), "Embed URLs must end in #embed fragment.")

    def test_01_224_embed_inner_keeps_narration_url_token_free(self):
        """Scenario 1.224: model narration strips token while widget payload keeps it.

        Given: An MCP principal with an access token.
        When: The embed tool produces its text + structured payload.
        Then: Structured embed_url contains auth_token, but narration link shown
        to the model does not leak that token.
        """
        with _embed_tool_with_stubs() as embed_tool, mock.patch.dict(
            sys.modules,
            {
                "lex.mcp_server.context": SimpleNamespace(
                    current_principal=lambda: SimpleNamespace(access_token="secret-token")
                )
            },
            clear=False,
        ):
            if asyncio.iscoroutinefunction(embed_tool._embed_view_inner):
                payload = _run_coroutine_safely(
                    embed_tool._embed_view_inner(path="calculation_log_tree")
                )
            else:
                payload = embed_tool._embed_view_inner(path="calculation_log_tree")

        narration = payload[0].text
        structured = json.loads(payload[1].text)
        embed_url = structured["embed_url"]

        self.assertIn(
            "auth_token=secret-token",
            embed_url,
            "Widget payload must include auth token for iframe bootstrap.",
        )
        self.assertNotIn(
            "auth_token=",
            narration,
            "Narration shown to model/user must not leak auth token.",
        )


class TestCluster01ab_McpModeAndVerificationContract(TestCase):
    def test_01_225_invoke_switch_rejects_unknown_mode(self):
        """Scenario 1.225: unsupported MCP mode is rejected with ValueError.

        Given: A mode outside the supported MCP mode set.
        When: switch-to-mode invocation is requested.
        Then: The call fails fast before any filesystem or process changes.
        """
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                mcp_mode_invoke.invoke_switch_to_mode(
                    "banana",
                    project_root=Path(tmp),
                    mcp_config_path=Path(tmp) / "mcp.json",
                )

    def test_01_226_verify_aligns_runtime_to_project_env_mode(self):
        """Scenario 1.226: ai-verify aligns runtime mode to project ``.env`` mode.

        Given: Project ``.env`` says forward, runtime mcp.json says backward.
        When: verify_ai_assets runs with align_mcp_mode enabled.
        Then: It invokes switch-to-mode toward the ``.env`` value before verify.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env_path = root / ".env"
            mcp_path = root / "mcp.json"
            env_path.write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            mcp_path.write_text("{}", encoding="utf-8")

            with mock.patch(
                "lex.tools.mcp_mode_invoke.invoke_switch_to_mode"
            ) as invoke_mock, mock.patch.object(
                verify_module,
                "_read_override_mode",
                return_value=None,
            ), mock.patch.object(
                verify_module,
                "_read_mode_from_mcp_json",
                return_value="backward",
            ), mock.patch.object(
                verify_module,
                "resolve_active_mcp_mode",
                return_value=("forward", "project-dotenv"),
            ), mock.patch.object(
                verify_module,
                "resolve_active_python_executable",
                return_value=Path("python"),
            ), mock.patch.object(
                verify_module,
                "_resolve_package_directory",
                return_value=None,
            ), mock.patch.object(
                verify_module,
                "resolve_lex_app_package_root",
                return_value=None,
            ):
                result = verify_module.verify_ai_assets(
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
            "Mode alignment must target the project .env LEX_MCP_MODE value.",
        )
        self.assertEqual(result.mode, "forward", "Resolved verify mode must remain forward.")


class TestCluster01ab_SetupWithAiProjectRootContract(TestCase):
    def test_01_227_configure_ai_integration_writes_to_given_project_root(self):
        """Scenario 1.227: setup uses provided project root directly.

        Given: A nested project root path.
        When: configure_ai_integration is executed.
        Then: .env and onboarding targets are rooted at that exact path.
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "nested" / "project"
            project_root.mkdir(parents=True, exist_ok=True)
            mcp_path = project_root / "mcp.json"

            config_entry = SimpleNamespace(
                path=str(mcp_path),
                written=True,
                created=False,
                error=None,
            )
            onboarding_result = SimpleNamespace(
                configs=(config_entry,),
                files_written=(),
                notes=(),
                payloads=(),
            )
            onboarding_module = SimpleNamespace(
                onboard_project=mock.Mock(return_value=onboarding_result),
            )
            real_import_module = importlib.import_module

            def _import_module_side_effect(name, *args, **kwargs):
                if name == "lex_mcp.ai_onboarding":
                    return onboarding_module
                return real_import_module(name, *args, **kwargs)

            with mock.patch.object(
                setup_with_ai_module, "resolve_active_python_executable", return_value=Path("/venv/bin/python")
            ), mock.patch.object(
                setup_with_ai_module, "resolve_wrapper_script_path", return_value=Path("/venv/bin/lex-mcp-local")
            ), mock.patch.object(
                setup_with_ai_module, "copy_lex_app_docs_directory", return_value=None
            ), mock.patch.object(
                setup_with_ai_module, "resolve_lex_app_package_root", return_value=None
            ), mock.patch.object(
                setup_with_ai_module, "build_ai_env_values", return_value={"LEX_MCP_MODE": "forward"}
            ), mock.patch.object(
                setup_with_ai_module, "update_env_file"
            ) as update_env_file_mock, mock.patch.object(
                setup_with_ai_module, "build_mcp_server_definition", return_value={"type": "stdio"}
            ), mock.patch(
                "importlib.import_module",
                side_effect=_import_module_side_effect,
            ):
                artifacts = setup_with_ai_module.configure_ai_integration(
                    project_root=project_root,
                    github_token="gh-token",
                    remote_mcp_api_key="remote-key",
                    mcp_config_path=mcp_path,
                    verify_server=False,
                )

        update_env_file_mock.assert_called_once()
        written_env_path = update_env_file_mock.call_args.args[0]
        self.assertEqual(
            written_env_path,
            (project_root / ".env").resolve(),
            "AI setup must write .env in the exact provided project root.",
        )

        onboarding_module.onboard_project.assert_called_once()
        onboard_root = onboarding_module.onboard_project.call_args.args[0]
        self.assertEqual(
            onboard_root,
            project_root,
            "Environment onboarding must run against the provided project root.",
        )
        self.assertEqual(
            artifacts.env_file_path,
            (project_root / ".env").resolve(),
            "Returned artifacts must point to the provided project root .env path.",
        )
