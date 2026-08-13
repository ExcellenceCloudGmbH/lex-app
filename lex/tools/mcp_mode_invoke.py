"""Compatibility shim.

:func:`invoke_switch_to_mode` now lives in :mod:`lex_mcp.mode_switch`, shipped
by the ``lex-mcp-local`` package, next to the primitives it drives. Run
``lex setup-with-ai`` to install it. This shim re-exports it so legacy imports
like ``from lex.tools.mcp_mode_invoke import invoke_switch_to_mode`` keep
working.

The version that lived here carried a second implementation of those
primitives, for the case where ``lex_mcp.mode_switch`` could not be imported.
That fallback reached into ``lex.tools.ai_dashboard``, which is itself now a
shim over lex-mcp-local — so the path it existed to cover could not have run.
There is one implementation now, and it is the one the MCP server uses.
"""
from __future__ import annotations

try:
    from lex_mcp import mode_switch as _impl
except ImportError as _err:  # pragma: no cover - defensive
    raise ImportError(
        "This lex-app needs a newer lex-mcp-local: lex_mcp.mode_switch is unavailable in "
        "the installed one. Run `lex ai-update` to upgrade it, or "
        "`lex setup-with-ai` if lex-mcp-local is not installed at all."
    ) from _err

from lex_mcp.mode_switch import (  # noqa: F401
    InvokeSwitchResult,
    invoke_switch_to_mode,
)
from lex_mcp.payload import MODE_TO_PACKAGE as _MODE_TO_PACKAGE

#: Kept for callers that imported it from here. Derived, never restated.
SUPPORTED_MCP_MODES: tuple[str, ...] = tuple(_MODE_TO_PACKAGE)


def __getattr__(name):
    return getattr(_impl, name)


def __dir__():
    return dir(_impl)
