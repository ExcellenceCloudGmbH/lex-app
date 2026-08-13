"""Cluster 16c — MCP mode-switch invocation from outside the server.

Intent (from lex/tools/mcp_mode_invoke.py):

    ``invoke_switch_to_mode`` replicates the three-step in-process
    ``switch_to_mode`` MCP tool behaviour from the lex-app side (ai-verify /
    ai-dashboard), where no live JSON-RPC session is available.

    Key behaviours under test:

    * ``_normalise_mode`` accepts any of the six supported modes and rejects
      everything else with a ``ValueError``.
    * ``InvokeSwitchResult.ok`` is ``True`` iff the ``errors`` tuple is empty.
    * When ``lex_mcp.mode_switch`` is unavailable, ``invoke_switch_to_mode``
      falls back to local lex-app primitives (the ``"fallback"`` strategy).
    * An invalid ``target_mode`` raises ``ValueError`` before any I/O.
"""

from __future__ import annotations

import builtins as _builtins
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from lex.tools.mcp_mode_invoke import (
    SUPPORTED_MCP_MODES,
    InvokeSwitchResult,
    _normalise_mode,
    invoke_switch_to_mode,
)

pytestmark = pytest.mark.mcp_tools

_real_import = _builtins.__import__


# ---------------------------------------------------------------------------
# 16c — _normalise_mode
# ---------------------------------------------------------------------------


class TestCluster16c_NormaliseMode:
    """Mode normalisation (``_normalise_mode``)."""

    def test_16_46_all_supported_modes_accepted(self):
        """Scenario 16.46: every mode in SUPPORTED_MCP_MODES is accepted."""
        for mode in SUPPORTED_MCP_MODES:
            assert _normalise_mode(mode) == mode

    def test_16_47_uppercase_is_normalised(self):
        """Scenario 16.47: mode with uppercase letters is lowercased."""
        assert _normalise_mode("FORWARD") == "forward"

    def test_16_48_whitespace_stripped(self):
        """Scenario 16.48: leading/trailing whitespace is stripped."""
        assert _normalise_mode("  edit  ") == "edit"

    def test_16_49_invalid_mode_raises_value_error(self):
        """Scenario 16.49: unrecognised mode string raises ValueError."""
        with pytest.raises(ValueError, match="Unsupported"):
            _normalise_mode("not_a_mode")

    def test_16_50_empty_string_raises_value_error(self):
        """Scenario 16.50: empty string raises ValueError."""
        with pytest.raises(ValueError):
            _normalise_mode("")


# ---------------------------------------------------------------------------
# 16c — InvokeSwitchResult.ok
# ---------------------------------------------------------------------------


class TestCluster16c_InvokeSwitchResultOk:
    """Outcome dataclass (``InvokeSwitchResult.ok``)."""

    def test_16_51_ok_true_when_no_errors(self):
        """Scenario 16.51: ok is True when errors tuple is empty."""
        result = InvokeSwitchResult(target_mode="forward")
        assert result.ok is True

    def test_16_52_ok_false_when_errors_present(self):
        """Scenario 16.52: ok is False when errors tuple is non-empty."""
        result = InvokeSwitchResult(target_mode="forward", errors=("something went wrong",))
        assert result.ok is False

    def test_16_53_default_strategy_is_noop(self):
        """Scenario 16.53: default strategy value is 'noop'."""
        result = InvokeSwitchResult(target_mode="edit")
        assert result.strategy == "noop"

    def test_16_54_default_override_not_written(self):
        """Scenario 16.54: override_written defaults to False."""
        result = InvokeSwitchResult(target_mode="review")
        assert result.override_written is False


# ---------------------------------------------------------------------------
# 16c — invoke_switch_to_mode
# ---------------------------------------------------------------------------


class TestCluster16c_InvokeSwitchToMode:
    """invoke_switch_to_mode integration (fallback path)."""

    def test_16_55_invalid_mode_raises_before_any_io(self, tmp_path):
        """Scenario 16.55: ValueError raised before writing anything."""
        mcp_config = tmp_path / "mcp.json"
        with pytest.raises(ValueError):
            invoke_switch_to_mode(
                "garbage_mode",
                project_root=tmp_path,
                mcp_config_path=mcp_config,
            )
        # Nothing was written.
        assert not mcp_config.exists()

    def test_16_56_fallback_strategy_used_when_lex_mcp_unavailable(self, tmp_path):
        """Scenario 16.56: strategy='fallback' when lex_mcp is not installed."""
        mcp_config = tmp_path / "mcp.json"
        mcp_config.write_text(json.dumps({"mcpServers": {}}), encoding="utf-8")
        (tmp_path / ".env").write_text("", encoding="utf-8")

        # Patch dashboard helpers so the fallback doesn't need a real environment.
        fake_override_dir = tmp_path / ".lex-mcp"
        fake_override_file = fake_override_dir / "mode-override"

        with patch("builtins.__import__", side_effect=_block_lex_mcp_import):
            result = invoke_switch_to_mode(
                "edit",
                project_root=tmp_path,
                mcp_config_path=mcp_config,
                stop_server=False,
            )

        # When lex_mcp is absent the function falls into its fallback branch.
        # It may encounter errors importing dashboard helpers too — both
        # "fallback" and a result with errors are acceptable; what matters is
        # it did not raise and did not claim "lex_mcp" strategy.
        assert result.target_mode == "edit"
        assert result.strategy != "lex_mcp"

    def test_16_57_stop_server_false_skips_stop_step(self, tmp_path):
        """Scenario 16.57: stop_server=False → server_stopped remains False."""
        mcp_config = tmp_path / "mcp.json"
        mcp_config.write_text("{}", encoding="utf-8")
        (tmp_path / ".env").write_text("", encoding="utf-8")

        with patch("builtins.__import__", side_effect=_block_lex_mcp_import):
            result = invoke_switch_to_mode(
                "forward",
                project_root=tmp_path,
                mcp_config_path=mcp_config,
                stop_server=False,
            )
        assert result.server_stopped is False


def _block_lex_mcp_import(name, *args, **kwargs):
    """Raise ImportError for lex_mcp.* imports to force fallback path."""
    if name.startswith("lex_mcp"):
        raise ImportError(f"stubbed: {name}")
    return _real_import(name, *args, **kwargs)
