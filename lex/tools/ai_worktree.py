"""Compatibility shim.

The implementation of this module lives in :mod:`lex_mcp.ai_worktree`,
shipped by the ``lex-mcp-local`` package. Run ``lex setup-with-ai`` to
install it. This shim re-exports the real module so imports like
``from lex.tools.ai_worktree import launch_ai_worktree`` resolve here the
same way they do for every sibling ``ai_*`` module.
"""
from __future__ import annotations

try:
    from lex_mcp import ai_worktree as _impl
except ImportError as _err:  # pragma: no cover - defensive
    raise ImportError(
        "lex-mcp-local is not installed. Run `lex setup-with-ai` to install it."
    ) from _err

from lex_mcp.ai_worktree import *  # noqa: F401,F403


def __getattr__(name):
    return getattr(_impl, name)


def __dir__():
    return dir(_impl)
