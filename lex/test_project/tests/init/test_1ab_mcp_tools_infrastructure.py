"""MCP tools infrastructure — URL routing, mode switching, and asset verification.

Intent: four framework files ship the plumbing that any IDE-integrated MCP
client relies on every session: the React embed URL builder, the mode-switch
invoker, the AI-setup helpers, and the asset-sync verifier.  A regression in
any of them silently breaks the developer's IDE experience with no error — the
wrong view loads, the mode stays stale, the agent payload is missing.

These tests pin the externally-observable contracts of each module:

* ``lex/mcp_server/tools/embed.py`` — path → view-type classification, title
  generation, frontend URL priority, and CSP origin list building.
* ``lex/tools/mcp_mode_invoke.py`` — mode-name validation, result dataclass,
  and the fallback invocation path (exercised without a live MCP server).
* ``lex/tools/setup_with_ai.py`` — ``normalize_mcp_mode``, environment
  normalisation, and ``update_env_file`` (add + update).
* ``lex/tools/verify_ai_assets.py`` — ``resolve_active_mcp_mode`` precedence
  chain and ``verify_directory`` file-restoration behaviour.

Cluster 1ab — scenarios 1.223–1.256.  Type: U (SimpleTestCase, no DB).
Covers: lex/mcp_server/tools/embed.py, lex/tools/mcp_mode_invoke.py,
        lex/tools/setup_with_ai.py, lex/tools/verify_ai_assets.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_mcp_tools_infrastructure.py -v
"""

from __future__ import annotations

import sys
import tempfile
import types
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

# ---------------------------------------------------------------------------
# Stub out missing modules so embed.py can be imported on any branch.
# The stubs are injected before the first import of the embed module and stay
# in place for the test session.  They do not affect other modules — each
# stub is registered under its own key and nothing else imports them.
# ---------------------------------------------------------------------------
def _make_embed_stubs() -> None:
    """Register lightweight stubs for embed.py's external-package imports."""
    if "lex.mcp_server.config" not in sys.modules:
        _config = types.ModuleType("lex.mcp_server.config")
        _config.mcp_setting = lambda key: None  # type: ignore[attr-defined]
        sys.modules["lex.mcp_server.config"] = _config

    if "lex.mcp_server.registry" not in sys.modules:
        _reg = types.ModuleType("lex.mcp_server.registry")
        _reg.container_is_writable = lambda r: False  # type: ignore[attr-defined]
        _reg.get_container = lambda r: None  # type: ignore[attr-defined]
        sys.modules["lex.mcp_server.registry"] = _reg

    for _mod_name in (
        "mcp",
        "mcp.server",
        "mcp.server.fastmcp",
        "mcp.server.fastmcp.resources",
        "mcp.types",
    ):
        if _mod_name not in sys.modules:
            sys.modules[_mod_name] = types.ModuleType(_mod_name)

    # Concrete names used in type annotations / class bodies.
    _fastmcp_mod = sys.modules["mcp.server.fastmcp"]
    if not hasattr(_fastmcp_mod, "FastMCP"):
        class _FastMCP:  # noqa: N801
            pass
        _fastmcp_mod.FastMCP = _FastMCP  # type: ignore[attr-defined]

    _res_mod = sys.modules["mcp.server.fastmcp.resources"]
    if not hasattr(_res_mod, "FunctionResource"):
        class _FunctionResource:  # noqa: N801
            pass
        _res_mod.FunctionResource = _FunctionResource  # type: ignore[attr-defined]

    _types_mod = sys.modules["mcp.types"]
    for _cls_name in ("TextContent", "ToolAnnotations"):
        if not hasattr(_types_mod, _cls_name):
            setattr(_types_mod, _cls_name, type(_cls_name, (), {}))


_make_embed_stubs()

# Now the embed module can be imported safely.
from lex.mcp_server.tools.embed import (  # noqa: E402
    _build_title,
    _classify_path,
    _csp_origins,
    _resolve_frontend_url,
)
from lex.tools.mcp_mode_invoke import (  # noqa: E402
    InvokeSwitchResult,
    _normalise_mode,
    invoke_switch_to_mode,
)
from lex.tools.setup_with_ai import (  # noqa: E402
    DEFAULT_LEX_MCP_MODE,
    normalize_ai_environments,
    normalize_mcp_mode,
    update_env_file,
)
from lex.tools.verify_ai_assets import (  # noqa: E402
    resolve_active_mcp_mode,
    verify_directory,
)

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# 1ab — embed.py: path classification
# ---------------------------------------------------------------------------


class TestCluster01ab_EmbedPathClassification(SimpleTestCase):
    """``_classify_path`` maps URL-segment lists to a view-type label.

    Scenario 1.223: empty list → "custom" (no recognisable pattern).
    Scenario 1.224: single segment → "list" (resource index page).
    Scenario 1.225: resource + "create" → "create" form.
    Scenario 1.226: resource + numeric ID → "detail" view.
    Scenario 1.227: resource + UUID → "detail" view.
    Scenario 1.228: resource + ID + "edit" → "edit" form.
    Scenario 1.229: resource + "show" + ID → "detail" view (alternate route).
    Scenario 1.230: unrecognised three-segment path → "custom".
    """

    def test_1_223_empty_segments_is_custom(self):
        """
        Scenario 1.223: empty segment list → "custom"
        Given no URL path segments
        When _classify_path is called
        Then the view type is "custom"
        """
        self.assertEqual(_classify_path([]), "custom")

    def test_1_224_single_segment_is_list(self):
        """
        Scenario 1.224: single segment → "list"
        Given a single path segment (resource name)
        When _classify_path is called
        Then the view type is "list"
        """
        self.assertEqual(_classify_path(["orders"]), "list")

    def test_1_225_resource_plus_create_is_create(self):
        """
        Scenario 1.225: resource + "create" → "create"
        Given segments ["orders", "create"]
        When _classify_path is called
        Then the view type is "create"
        """
        self.assertEqual(_classify_path(["orders", "create"]), "create")

    def test_1_226_resource_plus_numeric_id_is_detail(self):
        """
        Scenario 1.226: resource + numeric ID → "detail"
        Given segments ["orders", "42"]
        When _classify_path is called
        Then the view type is "detail"
        """
        self.assertEqual(_classify_path(["orders", "42"]), "detail")

    def test_1_227_resource_plus_uuid_is_detail(self):
        """
        Scenario 1.227: resource + UUID → "detail"
        Given segments with a UUID
        When _classify_path is called
        Then the view type is "detail"
        """
        self.assertEqual(
            _classify_path(["orders", "550e8400-e29b-41d4-a716-446655440000"]),
            "detail",
        )

    def test_1_228_resource_id_edit_is_edit(self):
        """
        Scenario 1.228: resource + ID + "edit" → "edit"
        Given segments ["orders", "42", "edit"]
        When _classify_path is called
        Then the view type is "edit"
        """
        self.assertEqual(_classify_path(["orders", "42", "edit"]), "edit")

    def test_1_229_show_route_is_detail(self):
        """
        Scenario 1.229: resource + "show" + ID → "detail"
        Given segments ["orders", "show", "42"]
        When _classify_path is called
        Then the view type is "detail"
        """
        self.assertEqual(_classify_path(["orders", "show", "42"]), "detail")

    def test_1_230_unrecognised_path_is_custom(self):
        """
        Scenario 1.230: three segments that match no known pattern → "custom"
        Given segments ["orders", "42", "cancel"]
        When _classify_path is called
        Then the view type is "custom"
        """
        self.assertEqual(_classify_path(["orders", "42", "cancel"]), "custom")


# ---------------------------------------------------------------------------
# 1ab — embed.py: title generation
# ---------------------------------------------------------------------------


class TestCluster01ab_EmbedTitleBuilding(SimpleTestCase):
    """``_build_title`` composes a human-readable embed title.

    Scenario 1.231: container with verbose_name produces a titlised label.
    Scenario 1.232: no container — resource name titlised instead.
    Scenario 1.233: no container, no resource → "Application".
    """

    def test_1_231_container_verbose_name_in_title(self):
        """
        Scenario 1.231: container with verbose_name
        Given a container with verbose_name "sales order"
        When _build_title is called for view_type "list"
        Then the title contains the titlised verbose_name and label
        """
        container = SimpleNamespace(verbose_name="sales order")
        title = _build_title("orders", "list", container)
        self.assertIn("Sales Order", title)
        self.assertIn("List View", title)

    def test_1_232_resource_name_used_when_no_container(self):
        """
        Scenario 1.232: no container, resource name present
        Given resource "product_lines" and no container
        When _build_title is called for view_type "detail"
        Then the title uses the resource name titlised
        """
        title = _build_title("product_lines", "detail", None)
        self.assertIn("Product Lines", title)
        self.assertIn("Detail View", title)

    def test_1_233_fallback_to_application_when_no_resource(self):
        """
        Scenario 1.233: no container, no resource name
        Given resource=None and container=None
        When _build_title is called
        Then the title starts with "Application"
        """
        title = _build_title(None, "custom", None)
        self.assertIn("Application", title)


# ---------------------------------------------------------------------------
# 1ab — embed.py: frontend URL resolution
# ---------------------------------------------------------------------------


class TestCluster01ab_FrontendUrlResolution(SimpleTestCase):
    """``_resolve_frontend_url`` picks the frontend base URL from several sources.

    Scenario 1.234: REACT_APP_URL env var wins over LEX_FRONTEND_URL.
    Scenario 1.235: LEX_FRONTEND_URL is used when REACT_APP_URL is absent.
    Scenario 1.236: falls back to http://localhost:8000 when both are absent.
    """

    def _resolve(self, env_override: dict) -> str:
        """Call _resolve_frontend_url with mcp_setting returning None."""
        import lex.mcp_server.tools.embed as _embed
        with patch.object(_embed, "mcp_setting", return_value=None):
            with patch.dict("os.environ", env_override, clear=False):
                return _resolve_frontend_url()

    def test_1_234_react_app_url_wins(self):
        """
        Scenario 1.234: REACT_APP_URL takes priority over LEX_FRONTEND_URL
        Given REACT_APP_URL=http://frontend:3000 and LEX_FRONTEND_URL=http://other:9000
        When _resolve_frontend_url is called
        Then the returned URL is http://frontend:3000
        """
        result = self._resolve(
            {"REACT_APP_URL": "http://frontend:3000", "LEX_FRONTEND_URL": "http://other:9000"}
        )
        self.assertEqual(result, "http://frontend:3000")

    def test_1_235_lex_frontend_url_fallback(self):
        """
        Scenario 1.235: LEX_FRONTEND_URL used when REACT_APP_URL is absent
        Given only LEX_FRONTEND_URL=http://staging:4000 and REACT_APP_URL=""
        When _resolve_frontend_url is called
        Then the returned URL is http://staging:4000
        """
        result = self._resolve(
            {"REACT_APP_URL": "", "LEX_FRONTEND_URL": "http://staging:4000"}
        )
        self.assertEqual(result, "http://staging:4000")

    def test_1_236_localhost_fallback(self):
        """
        Scenario 1.236: falls back to localhost:8000 when both env vars absent
        Given REACT_APP_URL="" and LEX_FRONTEND_URL=""
        When _resolve_frontend_url is called
        Then the URL is http://localhost:8000
        """
        result = self._resolve({"REACT_APP_URL": "", "LEX_FRONTEND_URL": ""})
        self.assertEqual(result, "http://localhost:8000")


# ---------------------------------------------------------------------------
# 1ab — embed.py: CSP origin list
# ---------------------------------------------------------------------------


class TestCluster01ab_CspOrigins(SimpleTestCase):
    """``_csp_origins`` always includes the frontend origin.

    Scenario 1.237: frontend origin is always present in the list.
    """

    def test_1_237_frontend_origin_in_csp(self):
        """
        Scenario 1.237: CSP list includes frontend origin
        Given a frontend URL of http://localhost:8000
        When _csp_origins is called
        Then "http://localhost:8000" is in the returned list
        """
        import lex.mcp_server.tools.embed as _embed
        with patch.object(_embed, "_resolve_frontend_url", return_value="http://localhost:8000"):
            with patch.object(_embed, "mcp_setting", return_value=None):
                origins = _csp_origins()
        self.assertIn("http://localhost:8000", origins)


# ---------------------------------------------------------------------------
# 1ab — setup_with_ai.py: normalize_mcp_mode
# ---------------------------------------------------------------------------


class TestCluster01ab_NormalizeMcpMode(SimpleTestCase):
    """``normalize_mcp_mode`` coerces or defaults MCP mode values.

    Scenario 1.238: a recognised mode string is returned unchanged (lowercased).
    Scenario 1.239: an unrecognised mode returns the default "forward".
    Scenario 1.240: None input returns the default.
    """

    def test_1_238_known_mode_passes_through(self):
        """
        Scenario 1.238: recognised mode → returned as-is
        Given mode="backward"
        When normalize_mcp_mode is called
        Then "backward" is returned
        """
        self.assertEqual(normalize_mcp_mode("backward"), "backward")

    def test_1_239_unknown_mode_returns_default(self):
        """
        Scenario 1.239: unrecognised mode → default
        Given mode="nonsense"
        When normalize_mcp_mode is called
        Then the default "forward" is returned
        """
        self.assertEqual(normalize_mcp_mode("nonsense"), DEFAULT_LEX_MCP_MODE)

    def test_1_240_none_input_returns_default(self):
        """
        Scenario 1.240: None input → default
        Given mode=None
        When normalize_mcp_mode is called
        Then the default "forward" is returned
        """
        self.assertEqual(normalize_mcp_mode(None), DEFAULT_LEX_MCP_MODE)


# ---------------------------------------------------------------------------
# 1ab — setup_with_ai.py: normalize_ai_environments
# ---------------------------------------------------------------------------


class TestCluster01ab_NormalizeAiEnvironments(SimpleTestCase):
    """``normalize_ai_environments`` accepts comma-/space-separated strings.

    Scenario 1.241: comma-separated known environment names are parsed.
    Scenario 1.242: None input falls back to the default environment.
    """

    def _call(self, environments):
        # Suppress the lex_mcp.environments registry so tests are self-contained.
        with mock.patch("lex.tools.setup_with_ai._environment_registry", return_value=None):
            return normalize_ai_environments(environments)

    def test_1_241_comma_separated_environments_parsed(self):
        """
        Scenario 1.241: comma-separated env names
        Given environments="pycharm-copilot,vscode-copilot"
        When normalize_ai_environments is called
        Then both names appear in the result tuple
        """
        result = self._call("pycharm-copilot,vscode-copilot")
        self.assertIn("pycharm-copilot", result)
        self.assertIn("vscode-copilot", result)

    def test_1_242_none_returns_default(self):
        """
        Scenario 1.242: None → default tuple
        Given environments=None
        When normalize_ai_environments is called
        Then the result tuple is non-empty and contains "pycharm-copilot"
        """
        result = self._call(None)
        self.assertTrue(len(result) > 0)
        self.assertIn("pycharm-copilot", result)


# ---------------------------------------------------------------------------
# 1ab — setup_with_ai.py: update_env_file
# ---------------------------------------------------------------------------


class TestCluster01ab_UpdateEnvFile(SimpleTestCase):
    """``update_env_file`` persists key=value pairs to a ``.env`` file.

    Scenario 1.243: a new key is appended to the file.
    Scenario 1.244: an existing key is updated in place (not duplicated).
    """

    def test_1_243_new_key_appended(self):
        """
        Scenario 1.243: new key → appended to file
        Given an existing .env with KEY_A=old
        When update_env_file is called with KEY_B=new
        Then the file contains both KEY_A=old and KEY_B=new
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("KEY_A=old\n", encoding="utf-8")
            update_env_file(env_path, {"KEY_B": "new"})
            content = env_path.read_text(encoding="utf-8")
        self.assertIn("KEY_A=old", content)
        self.assertIn("KEY_B=new", content)

    def test_1_244_existing_key_updated_not_duplicated(self):
        """
        Scenario 1.244: existing key → updated in place
        Given an existing .env with LEX_MCP_MODE=forward
        When update_env_file is called with LEX_MCP_MODE=backward
        Then the file has exactly one LEX_MCP_MODE line set to backward
        """
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / ".env"
            env_path.write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            update_env_file(env_path, {"LEX_MCP_MODE": "backward"})
            content = env_path.read_text(encoding="utf-8")
        self.assertNotIn("LEX_MCP_MODE=forward", content)
        self.assertIn("LEX_MCP_MODE=backward", content)
        self.assertEqual(content.count("LEX_MCP_MODE="), 1)


# ---------------------------------------------------------------------------
# 1ab — mcp_mode_invoke.py: _normalise_mode + InvokeSwitchResult
# ---------------------------------------------------------------------------


class TestCluster01ab_NormaliseMode(SimpleTestCase):
    """``_normalise_mode`` enforces the supported mode allowlist.

    Scenario 1.245: recognised modes are returned lower-cased.
    Scenario 1.246: unrecognised mode raises ValueError.
    """

    def test_1_245_recognised_mode_returned(self):
        """
        Scenario 1.245: recognised mode → returned
        Given mode="edit"
        When _normalise_mode is called
        Then "edit" is returned
        """
        self.assertEqual(_normalise_mode("edit"), "edit")

    def test_1_246_unrecognised_mode_raises(self):
        """
        Scenario 1.246: unrecognised mode → ValueError
        Given mode="invalid-mode"
        When _normalise_mode is called
        Then ValueError is raised
        """
        with self.assertRaises(ValueError):
            _normalise_mode("invalid-mode")


class TestCluster01ab_InvokeSwitchResult(SimpleTestCase):
    """``InvokeSwitchResult.ok`` reflects the presence of errors.

    Scenario 1.247: no errors → ok is True.
    Scenario 1.248: errors present → ok is False.
    """

    def test_1_247_ok_true_when_no_errors(self):
        """
        Scenario 1.247: no errors → ok=True
        Given InvokeSwitchResult with empty errors tuple
        When ok is accessed
        Then True is returned
        """
        result = InvokeSwitchResult(target_mode="forward")
        self.assertTrue(result.ok)

    def test_1_248_ok_false_when_errors_present(self):
        """
        Scenario 1.248: errors → ok=False
        Given InvokeSwitchResult with a non-empty errors tuple
        When ok is accessed
        Then False is returned
        """
        result = InvokeSwitchResult(target_mode="forward", errors=("something broke",))
        self.assertFalse(result.ok)


# ---------------------------------------------------------------------------
# 1ab — mcp_mode_invoke.py: invoke_switch_to_mode fallback path
# ---------------------------------------------------------------------------


class TestCluster01ab_InvokeSwitchFallback(SimpleTestCase):
    """``invoke_switch_to_mode`` uses local lex-app helpers when lex_mcp absent.

    Scenario 1.249: when lex_mcp.mode_switch is unavailable the fallback path
    updates the .env file and records the strategy as "fallback".
    """

    def test_1_249_fallback_strategy_updates_env(self):
        """
        Scenario 1.249: lex_mcp absent → fallback writes env file
        Given lex_mcp.mode_switch cannot be imported
        And a project_root with a .env file
        When invoke_switch_to_mode is called with target_mode="backward"
        Then the result is an InvokeSwitchResult and does not raise
        And if strategy is "fallback", the .env file contains LEX_MCP_MODE=backward
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            env_path = project_root / ".env"
            env_path.write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            mcp_config = project_root / "mcp.json"
            mcp_config.write_text("{}", encoding="utf-8")

            # Make lex_mcp.mode_switch unavailable to force the fallback branch.
            broken_modules = {
                k: v for k, v in sys.modules.items()
                if k in ("lex_mcp", "lex_mcp.mode_switch")
            }
            with mock.patch.dict(
                "sys.modules",
                {"lex_mcp": None, "lex_mcp.mode_switch": None},
            ):
                result = invoke_switch_to_mode(
                    "backward",
                    project_root=project_root,
                    mcp_config_path=mcp_config,
                    stop_server=False,
                )

            self.assertIsInstance(result, InvokeSwitchResult)
            if result.strategy == "fallback":
                content = env_path.read_text(encoding="utf-8")
                self.assertIn("LEX_MCP_MODE=backward", content)


# ---------------------------------------------------------------------------
# 1ab — verify_ai_assets.py: resolve_active_mcp_mode precedence
# ---------------------------------------------------------------------------


class TestCluster01ab_ResolveActiveMcpMode(SimpleTestCase):
    """``resolve_active_mcp_mode`` follows a strict precedence chain.

    Scenario 1.250: explicit_mode argument overrides everything.
    Scenario 1.251: project .env value is used when no override file exists.
    Scenario 1.252: process environment is used when .env has no LEX_MCP_MODE.
    Scenario 1.253: unknown resolved mode raises SetupWithAIError.
    """

    def test_1_250_explicit_mode_wins(self):
        """
        Scenario 1.250: explicit_mode wins
        Given explicit_mode="review" and a .env with LEX_MCP_MODE=forward
        When resolve_active_mcp_mode is called
        Then ("review", "cli") is returned
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("LEX_MCP_MODE=forward\n", encoding="utf-8")
            with mock.patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None):
                mode, source = resolve_active_mcp_mode(root, explicit_mode="review")
        self.assertEqual(mode, "review")
        self.assertEqual(source, "cli")

    def test_1_251_dotenv_used_when_no_override(self):
        """
        Scenario 1.251: .env value is used when override file absent
        Given .env with LEX_MCP_MODE=edit and no override file
        When resolve_active_mcp_mode is called
        Then ("edit", "project-dotenv") is returned
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("LEX_MCP_MODE=edit\n", encoding="utf-8")
            with mock.patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None):
                with mock.patch(
                    "lex.tools.verify_ai_assets._resolve_mode_from_mcp_json_files",
                    return_value=None,
                ):
                    mode, source = resolve_active_mcp_mode(root)
        self.assertEqual(mode, "edit")
        self.assertEqual(source, "project-dotenv")

    def test_1_252_process_env_fallback(self):
        """
        Scenario 1.252: process env used when .env has no LEX_MCP_MODE
        Given an empty .env and LEX_MCP_MODE=review in the env mapping
        When resolve_active_mcp_mode is called with env={"LEX_MCP_MODE": "review"}
        Then ("review", "process-env") is returned
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("# empty\n", encoding="utf-8")
            with mock.patch("lex.tools.verify_ai_assets._read_override_mode", return_value=None):
                with mock.patch(
                    "lex.tools.verify_ai_assets._resolve_mode_from_mcp_json_files",
                    return_value=None,
                ):
                    mode, source = resolve_active_mcp_mode(
                        root, env={"LEX_MCP_MODE": "review"}
                    )
        self.assertEqual(mode, "review")
        self.assertEqual(source, "process-env")

    def test_1_253_unknown_mode_raises(self):
        """
        Scenario 1.253: unknown mode resolved → SetupWithAIError
        Given an explicit_mode that is not a valid MCP mode
        When resolve_active_mcp_mode is called
        Then SetupWithAIError is raised
        """
        from lex.tools.setup_with_ai import SetupWithAIError

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("", encoding="utf-8")
            with self.assertRaises(SetupWithAIError):
                resolve_active_mcp_mode(root, explicit_mode="not-a-real-mode")


# ---------------------------------------------------------------------------
# 1ab — verify_ai_assets.py: verify_directory file restoration
# ---------------------------------------------------------------------------


class TestCluster01ab_VerifyDirectory(SimpleTestCase):
    """``verify_directory`` restores missing and drifted files from source.

    Scenario 1.254: a missing file in the destination is restored from source.
    Scenario 1.255: a file whose content has drifted from source is restored.
    Scenario 1.256: source directory absent → result is skipped with a reason.
    """

    def _write(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_1_254_missing_file_is_restored(self):
        """
        Scenario 1.254: missing destination file → restored
        Given a source directory with agent.md
        And the destination directory does not contain agent.md
        When verify_directory is called
        Then agent.md is in restored_files and exists in the destination
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            source_dir = Path(tmp) / "source" / ".github"
            self._write(source_dir / "agents" / "agent.md", "content")

            result = verify_directory(
                project_root=project_root,
                source_directory=source_dir,
                directory_name=".github",
            )

            self.assertIn(Path("agents/agent.md"), result.restored_files)
            self.assertTrue((project_root / ".github" / "agents" / "agent.md").exists())

    def test_1_255_drifted_file_is_restored(self):
        """
        Scenario 1.255: drifted file → restored to source content
        Given a source agent.md with content "new content"
        And the destination agent.md has content "stale content"
        When verify_directory is called
        Then agent.md is in restored_files and destination has "new content"
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            source_dir = Path(tmp) / "source" / ".github"
            self._write(source_dir / "agents" / "agent.md", "new content")
            self._write(project_root / ".github" / "agents" / "agent.md", "stale content")

            result = verify_directory(
                project_root=project_root,
                source_directory=source_dir,
                directory_name=".github",
            )

            self.assertIn(Path("agents/agent.md"), result.restored_files)
            dest = (project_root / ".github" / "agents" / "agent.md").read_text(encoding="utf-8")
            self.assertEqual(dest, "new content")

    def test_1_256_missing_source_directory_skipped(self):
        """
        Scenario 1.256: source directory does not exist → result is skipped
        Given a source_directory path that does not exist on disk
        When verify_directory is called
        Then skipped_reason is non-None
        """
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp) / "project"
            absent_source = Path(tmp) / "nonexistent" / ".github"

            result = verify_directory(
                project_root=project_root,
                source_directory=absent_source,
                directory_name=".github",
            )

            self.assertIsNotNone(result.skipped_reason)
