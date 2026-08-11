"""Compatibility shim.

The implementation of this module lives in :mod:`lex_mcp.ai_assets`, shipped by
the ``lex-mcp-local`` package. Run ``lex setup-with-ai`` to install it. This
shim re-exports the real module so legacy imports like
``from lex.tools.verify_ai_assets import verify_ai_assets`` keep working.

It moved because verification has to know which modes exist and which package
ships each one, and the copy that lived here went stale at six modes while the
server shipped nine. Keeping the table next to the server is the only way it
stays right.
"""
from __future__ import annotations

try:
    from lex_mcp import ai_assets as _impl
except ImportError as _err:  # pragma: no cover - defensive
    raise ImportError(
        "This lex-app needs a newer lex-mcp-local: lex_mcp.ai_assets is unavailable in "
        "the installed one. Run `lex ai-update` to upgrade it, or "
        "`lex setup-with-ai` if lex-mcp-local is not installed at all."
    ) from _err

from lex_mcp.ai_assets import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)


def __dir__():
    return dir(_impl)
