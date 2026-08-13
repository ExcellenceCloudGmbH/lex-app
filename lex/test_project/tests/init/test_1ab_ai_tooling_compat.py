"""Regression tests for AI setup / verify / MCP tooling compatibility.

Intent
------

PR #703 moves the MCP/AI setup and verification logic onto ``lex-mcp-local``
while keeping the ``lex-app`` entry points stable. The customer-visible intent,
grounded in ``docs/reference/CLI Commands.md``, is:

* ``lex setup-with-ai`` still offers the full advertised MCP mode roster,
  honours the environment choices the user makes in the browser form, resolves
  documented environment aliases, and targets the interpreter that actually owns
  the active project environment.
* ``lex ai-verify`` and the dashboard-side mode switch keep working through the
  legacy ``lex.tools.*`` import paths after the implementation moves into
  ``lex-mcp-local``.
* The MCP embed widget still registers on standalone FastMCP, which now expects
  ``Tool.from_function(...)`` rather than the SDK-vendored ``add_tool(fn,
  name=...)`` signature.

A regression here breaks the "AI commands configure and verify my project"
journey silently: setup renders the wrong options, the wrong IDEs get onboarded,
imports that used to work explode after upgrade, or the embed tool crashes when
it registers.

Cluster 1ab — scenarios 1.223–1.230. Type: U.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_ai_tooling_compat.py -v
"""

from __future__ import annotations

import importlib
import json
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import ModuleType, SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.tools import setup_with_ai

pytestmark = pytest.mark.init


@contextmanager
def _fresh_import(module_name: str, fake_modules: dict[str, ModuleType]):
    """Import *module_name* against temporary dependency modules."""
    parent_name, _, child_name = module_name.rpartition(".")
    parent_module = sys.modules.get(parent_name)
    if parent_module is not None:
        parent_module.__dict__.pop(child_name, None)
    sys.modules.pop(module_name, None)
    with patch.dict(sys.modules, fake_modules, clear=False):
        module = importlib.import_module(module_name)
        try:
            yield module
        finally:
            sys.modules.pop(module_name, None)
            if parent_module is not None:
                parent_module.__dict__.pop(child_name, None)


class _FakeToolAnnotations:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeFunctionResource:
    def __init__(self, **kwargs):
        self.__dict__.update(kwargs)


class _FakeTool:
    @staticmethod
    def from_function(*args, **kwargs):
        return {"args": args, "kwargs": kwargs}


class _FakeServer:
    def __init__(self):
        self.resources = []
        self.tools = []

    def add_resource(self, resource):
        self.resources.append(resource)

    def add_tool(self, tool):
        self.tools.append(tool)


class TestCluster01ab_SetupWithAI(TestCase):
    """Cluster 1ab: setup-with-ai keeps the documented setup surface stable."""

    def test_1_223_cold_start_mode_roster_matches_the_documented_modes(self):
        """
        Scenario 1.223: cold-start setup still exposes the full MCP mode roster.

        Given: ``lex-mcp-local`` is not importable yet during first-time setup
        When: ``lex setup-with-ai`` renders its fallback mode list
        Then: the newer documented modes stay available instead of silently
              disappearing from the picker
        """
        expected_modes = {
            "brief",
            "forward",
            "backward",
            "edit",
            "review",
            "test",
            "input",
            "deploy",
            "mvp_generator",
            "mvp_completion",
        }

        self.assertEqual(
            set(setup_with_ai.SUPPORTED_MCP_MODES),
            expected_modes,
            "Cold-start setup must advertise every supported MCP mode.",
        )
        self.assertTrue(
            expected_modes.issubset({card["value"] for card in setup_with_ai.MCP_MODE_CARD_DEFS}),
            "Every advertised mode must have a rendered card on the setup page.",
        )

    def test_1_224_environment_checkboxes_submit_through_the_setup_form(self):
        """
        Scenario 1.224: browser selections are owned by the setup form.

        Given: the environment cards sit outside the credentials ``<form>``
        When: the setup page is rendered
        Then: each checkbox names the owning form id, and the checked state
              mirrors the supplied selection instead of being dropped on submit
        """
        selected_html = setup_with_ai._build_setup_form_html(
            state="token",
            project_root=Path("."),
            env_file_path=Path(".env"),
            selected_environments=("vscode-copilot",),
        )
        empty_html = setup_with_ai._build_setup_form_html(
            state="token",
            project_root=Path("."),
            env_file_path=Path(".env"),
            selected_environments=(),
        )

        self.assertIn(
            f'id="{setup_with_ai.SETUP_FORM_ID}"',
            selected_html,
            "The setup page must render the stable form id the cards target.",
        )
        self.assertEqual(
            selected_html.count(f'form="{setup_with_ai.SETUP_FORM_ID}"'),
            len(setup_with_ai.SUPPORTED_AI_ENVIRONMENTS),
            "Every environment checkbox must name the setup form or the browser drops the selection.",
        )
        self.assertEqual(
            empty_html.count(f'form="{setup_with_ai.SETUP_FORM_ID}" checked'),
            0,
            "Clearing the environment selection must not re-check boxes on the rerender.",
        )

    def test_1_225_environment_aliases_resolve_and_unknown_values_surface(self):
        """
        Scenario 1.225: CLI environment names behave like the docs imply.

        Given: a user types shorthand names such as ``claude`` or ``vscode``
        When: setup normalises the requested environments
        Then: supported aliases resolve to their canonical keys, unknown names
              raise clearly, and non-strict read-back falls back safely
        """
        self.assertEqual(
            setup_with_ai.normalize_ai_environments("claude vscode"),
            ("claude-code", "vscode-copilot"),
            "Known aliases must resolve to the canonical environment keys.",
        )
        with self.assertRaises(setup_with_ai.SetupWithAIError) as caught:
            setup_with_ai.normalize_ai_environments("emacs")
        self.assertIn(
            "emacs",
            str(caught.exception),
            "Unknown environment errors must name the unsupported value.",
        )
        self.assertEqual(
            setup_with_ai.normalize_ai_environments("emacs", strict=False),
            (setup_with_ai.DEFAULT_AI_ENVIRONMENT,),
            "Reading back a drifted persisted value must degrade to the default instead of crashing.",
        )

    def test_1_226_running_virtualenv_outranks_path_python(self):
        """
        Scenario 1.226: setup installs into the interpreter that owns lex-app.

        Given: ``lex`` is running inside a virtualenv but ``python3`` on PATH is
               the system interpreter
        When: setup chooses the active Python executable
        Then: it keeps the running venv interpreter rather than silently
              installing ``lex-mcp-local`` into some other Python
        """
        with TemporaryDirectory() as tmp:
            venv_python = Path(tmp) / "venv" / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.touch()
            project = Path(tmp) / "project"
            project.mkdir()

            with patch.object(setup_with_ai.sys, "prefix", str(venv_python.parent.parent)), \
                 patch.object(setup_with_ai.sys, "base_prefix", "/usr"), \
                 patch.object(setup_with_ai.sys, "executable", str(venv_python)), \
                 patch.object(setup_with_ai.shutil, "which", return_value="/usr/bin/python3"):
                resolved = setup_with_ai.resolve_active_python_executable(project, env={})

        self.assertEqual(
            resolved,
            venv_python,
            "The running virtualenv interpreter must outrank PATH when lex is already inside that venv.",
        )

    def test_1_227_onboarding_stays_in_process_only_for_the_same_interpreter(self):
        """
        Scenario 1.227: onboarding uses the interpreter that can actually see the package.

        Given: one request targeting this interpreter and another targeting a
               different interpreter path
        When: setup invokes onboarding
        Then: the same-interpreter path uses the in-process module handler, while
              the foreign interpreter path shells out with the JSON protocol
        """
        fake_onboarding = SimpleNamespace(handle_request=lambda request: {"ok": True, "request": request})

        with patch.object(setup_with_ai, "_same_interpreter", return_value=True), \
             patch.object(setup_with_ai, "_onboarding_module", return_value=fake_onboarding):
            response, error = setup_with_ai.invoke_onboarding(sys.executable, "describe", project="demo")

        self.assertIsNone(error, "The in-process onboarding fast path must not report an error.")
        self.assertEqual(
            response,
            {"ok": True, "request": {"action": "describe", "project": "demo"}},
            "The same-interpreter path must hand the request straight to the onboarding module.",
        )

        completed = subprocess.CompletedProcess(
            ["/foreign/python", "-m", "lex_mcp.ai_onboarding"],
            0,
            stdout=json.dumps({"ok": True, "environments": ["codex"]}),
            stderr="",
        )
        with patch.object(setup_with_ai, "_same_interpreter", return_value=False), \
             patch.object(setup_with_ai.subprocess, "run", return_value=completed) as run_mock:
            response, error = setup_with_ai.invoke_onboarding("/foreign/python", "describe")

        self.assertIsNone(error, "A successful subprocess handoff must not report an error.")
        self.assertEqual(
            response,
            {"ok": True, "environments": ["codex"]},
            "The foreign-interpreter path must return the parsed JSON response from the subprocess.",
        )
        self.assertEqual(
            run_mock.call_args.args[0],
            ["/foreign/python", "-m", "lex_mcp.ai_onboarding"],
            "A foreign interpreter must be driven via ``python -m lex_mcp.ai_onboarding``.",
        )


class TestCluster01ab_AiCompatibilityShims(TestCase):
    """Cluster 1ab: legacy ``lex.tools`` import paths stay wired to lex-mcp-local."""

    def test_1_228_mcp_mode_invoke_reexports_the_lex_mcp_switch_api(self):
        """
        Scenario 1.228: legacy mode-switch imports still land on the real implementation.

        Given: a newer ``lex-mcp-local`` exposes ``lex_mcp.mode_switch`` and
               ``lex_mcp.payload.MODE_TO_PACKAGE``
        When: callers import ``lex.tools.mcp_mode_invoke``
        Then: the legacy module re-exports the same callable and derives its
              supported-mode list from the package rather than restating it
        """
        fake_package = ModuleType("lex_mcp")
        fake_package.__path__ = []
        fake_mode_switch = ModuleType("lex_mcp.mode_switch")
        fake_payload = ModuleType("lex_mcp.payload")

        def fake_invoke_switch_to_mode(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        class FakeInvokeSwitchResult:
            pass

        fake_mode_switch.invoke_switch_to_mode = fake_invoke_switch_to_mode
        fake_mode_switch.InvokeSwitchResult = FakeInvokeSwitchResult
        fake_mode_switch.extra_attr = "reachable-through-getattr"
        fake_payload.MODE_TO_PACKAGE = {"brief": "pkg.brief", "deploy": "pkg.deploy"}
        fake_package.mode_switch = fake_mode_switch

        fake_modules = {
            "lex_mcp": fake_package,
            "lex_mcp.mode_switch": fake_mode_switch,
            "lex_mcp.payload": fake_payload,
        }

        with _fresh_import("lex.tools.mcp_mode_invoke", fake_modules) as imported_mcp_mode_invoke:
            import lex.tools.mcp_mode_invoke as imported_mcp_mode_invoke

            self.assertIs(
                imported_mcp_mode_invoke.invoke_switch_to_mode,
                fake_invoke_switch_to_mode,
                "Legacy imports must point at lex-mcp-local's real mode-switch callable.",
            )
            self.assertIs(
                imported_mcp_mode_invoke.InvokeSwitchResult,
                FakeInvokeSwitchResult,
                "Legacy imports must keep exposing the real result type.",
            )
            self.assertEqual(
                imported_mcp_mode_invoke.SUPPORTED_MCP_MODES,
                ("brief", "deploy"),
                "Supported modes must be derived from lex_mcp.payload rather than copied into lex-app.",
            )
            self.assertEqual(
                imported_mcp_mode_invoke.extra_attr,
                "reachable-through-getattr",
                "__getattr__ must proxy additional mode-switch attributes for legacy callers.",
            )
            self.assertIn(
                "extra_attr",
                imported_mcp_mode_invoke.__dir__(),
                "__dir__ must expose the proxied implementation surface.",
            )

    def test_1_229_verify_ai_assets_reexports_the_lex_mcp_ai_assets_api(self):
        """
        Scenario 1.229: legacy verify imports still land on the real asset verifier.

        Given: a newer ``lex-mcp-local`` exposes ``lex_mcp.ai_assets``
        When: callers import ``lex.tools.verify_ai_assets``
        Then: the legacy module re-exports the real verifier module surface
        """
        fake_package = ModuleType("lex_mcp")
        fake_package.__path__ = []
        fake_ai_assets = ModuleType("lex_mcp.ai_assets")

        def fake_verify_ai_assets(*args, **kwargs):
            return {"args": args, "kwargs": kwargs}

        fake_ai_assets.verify_ai_assets = fake_verify_ai_assets
        fake_ai_assets.ALL_MCP_MODES = ("forward", "deploy")
        fake_ai_assets.extra_attr = "proxied"
        fake_ai_assets.__all__ = ["verify_ai_assets", "ALL_MCP_MODES"]
        fake_package.ai_assets = fake_ai_assets

        fake_modules = {
            "lex_mcp": fake_package,
            "lex_mcp.ai_assets": fake_ai_assets,
        }

        with _fresh_import("lex.tools.verify_ai_assets", fake_modules) as imported_verify_ai_assets:
            import lex.tools.verify_ai_assets as imported_verify_ai_assets

            self.assertIs(
                imported_verify_ai_assets.verify_ai_assets,
                fake_verify_ai_assets,
                "Legacy verify imports must point at lex-mcp-local's real verifier.",
            )
            self.assertEqual(
                imported_verify_ai_assets.ALL_MCP_MODES,
                ("forward", "deploy"),
                "Star-reexported module constants must remain visible through the legacy path.",
            )
            self.assertEqual(
                imported_verify_ai_assets.extra_attr,
                "proxied",
                "__getattr__ must proxy additional ai-assets attributes for legacy callers.",
            )
            self.assertIn(
                "extra_attr",
                imported_verify_ai_assets.__dir__(),
                "__dir__ must expose the proxied ai-assets surface.",
            )


class TestCluster01ab_McpEmbedCompatibility(TestCase):
    """Cluster 1ab: the MCP embed widget registers on standalone FastMCP."""

    def test_1_230_embed_register_uses_tool_from_function_with_widget_metadata(self):
        """
        Scenario 1.230: embed registration matches standalone FastMCP's tool API.

        Given: the standalone FastMCP API expects ``Tool.from_function(...)``
        When: ``lex.mcp_server.tools.embed.register`` wires the widget onto a server
        Then: it adds the resource and passes the wrapped tool object to
              ``add_tool``, preserving the widget resource URI metadata
        """
        fake_fastmcp = ModuleType("fastmcp")
        fake_fastmcp.FastMCP = object
        fake_fastmcp_resources = ModuleType("fastmcp.resources")
        fake_fastmcp_resources.FunctionResource = _FakeFunctionResource
        fake_fastmcp_tools = ModuleType("fastmcp.tools")
        fake_fastmcp_tools.Tool = _FakeTool
        fake_embed_config = ModuleType("lex.mcp_server.config")
        fake_embed_config.mcp_setting = lambda *_args, **_kwargs: None
        fake_embed_registry = ModuleType("lex.mcp_server.registry")
        fake_embed_registry.container_is_writable = lambda *_args, **_kwargs: False
        fake_embed_registry.get_container = lambda *_args, **_kwargs: None
        fake_mcp = ModuleType("mcp")
        fake_mcp_types = ModuleType("mcp.types")
        fake_mcp_types.TextContent = dict
        fake_mcp_types.ToolAnnotations = _FakeToolAnnotations

        fake_modules = {
            "fastmcp": fake_fastmcp,
            "fastmcp.resources": fake_fastmcp_resources,
            "fastmcp.tools": fake_fastmcp_tools,
            "lex.mcp_server.config": fake_embed_config,
            "lex.mcp_server.registry": fake_embed_registry,
            "mcp": fake_mcp,
            "mcp.types": fake_mcp_types,
        }

        with _fresh_import("lex.mcp_server.tools.embed", fake_modules) as embed_tool:
            import lex.mcp_server.tools.embed as embed_tool

            server = _FakeServer()
            wrapped_tool = object()

            with patch.object(embed_tool, "_csp_origins", return_value=["https://frontend.example"]), \
                 patch.object(embed_tool, "_resolve_frontend_url", return_value="https://frontend.example"), \
                 patch.object(embed_tool.Tool, "from_function", return_value=wrapped_tool) as from_function:
                embed_tool.register(server)

        self.assertEqual(
            len(server.resources),
            1,
            "Registering the embed widget must add exactly one UI resource.",
        )
        self.assertEqual(
            server.tools,
            [wrapped_tool],
            "FastMCP registration must pass the wrapped Tool object to add_tool.",
        )
        self.assertEqual(
            server.resources[0].uri,
            "ui://lex/embed-view.html",
            "The widget resource URI must stay stable for the MCP Apps bridge.",
        )
        self.assertEqual(
            server.resources[0].meta["ui"]["csp"]["frameDomains"],
            ["https://frontend.example"],
            "The widget CSP must be derived from the resolved frontend origin list.",
        )
        self.assertIs(
            from_function.call_args.args[0],
            embed_tool._embed_view,
            "The wrapped tool must be built from the real embed entry point.",
        )
        self.assertEqual(
            from_function.call_args.kwargs["meta"]["ui/resourceUri"],
            "ui://lex/embed-view.html",
            "Legacy flat widget metadata must still be published for older hosts.",
        )
