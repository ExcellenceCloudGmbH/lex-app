"""
Cluster 1ab: ``lex.tools.ai_worktree`` compatibility shim.

Intent
------

``lex/tools/ai_worktree.py`` is a thin compatibility shim that
re-exports ``lex_mcp.ai_worktree`` so that code written as::

    from lex.tools.ai_worktree import launch_ai_worktree

…resolves correctly regardless of whether the caller imports through
the ``lex.tools`` namespace or directly from the ``lex_mcp`` package.

Three externally-observable behaviours:

1. **Missing-dependency error** — when ``lex_mcp`` is not installed,
   importing the shim raises ``ImportError`` whose message names both
   the missing package and the recovery command so the operator knows
   exactly what to run.

2. **Attribute delegation via ``__getattr__``** — any attribute access
   on the shim module is forwarded to ``_impl`` (the real module).
   This is the mechanism that makes ``from lex.tools.ai_worktree import
   anything`` work for names that weren't imported explicitly.

3. **Directory delegation via ``__dir__``** — ``dir(lex.tools.ai_worktree)``
   returns whatever the real module exposes, so tab-completion and
   introspection tools see the right surface without knowing about the
   shim layer.

These are the same three behaviours provided by every sibling shim
(``ai_faq``, ``ai_dashboard``, ``ai_issue_report``), tested here so
the pattern is exercised for ``ai_worktree`` without relying on the
sibling tests.

All scenarios are pure-Python, no DB — they run in single-digit
milliseconds.

Scenario numbering picks up at **1.223** (1aa ended at 1.222).
"""
from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest import TestCase

import pytest

pytestmark = pytest.mark.init


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_ai_worktree_module():
    """Return a minimal synthetic ``lex_mcp.ai_worktree`` module."""
    mod = types.ModuleType("lex_mcp.ai_worktree")
    mod.launch_ai_worktree = lambda: "launched"
    mod.WORKTREE_VERSION = "1.0.0"
    return mod


def _make_fake_lex_mcp_package(ai_worktree_mod):
    """Return a minimal synthetic ``lex_mcp`` package."""
    pkg = types.ModuleType("lex_mcp")
    pkg.ai_worktree = ai_worktree_mod
    return pkg


class _ShimTestBase(TestCase):
    """Inject a synthetic ``lex_mcp`` package and force a clean shim reload.

    Each test class that needs a working shim inherits from this to avoid
    interference between tests that manipulate ``sys.modules``.
    """

    def setUp(self):
        # Remove any previously-loaded shim so the next import statement
        # runs the module body again against our synthetic lex_mcp.
        self._restore = {}
        for key in ("lex_mcp", "lex_mcp.ai_worktree", "lex.tools.ai_worktree"):
            self._restore[key] = sys.modules.pop(key, None)

        self._ai_worktree_mod = _make_fake_ai_worktree_module()
        pkg = _make_fake_lex_mcp_package(self._ai_worktree_mod)
        sys.modules["lex_mcp"] = pkg
        sys.modules["lex_mcp.ai_worktree"] = self._ai_worktree_mod

    def tearDown(self):
        # Put the world back the way we found it.
        for key in ("lex_mcp", "lex_mcp.ai_worktree", "lex.tools.ai_worktree"):
            sys.modules.pop(key, None)
        for key, value in self._restore.items():
            if value is not None:
                sys.modules[key] = value


# ---------------------------------------------------------------------------
# 1.223 — Missing-dependency error
# ---------------------------------------------------------------------------

class TestCluster01ab_MissingDependency(TestCase):
    """Importing the shim without ``lex_mcp`` installed raises
    ``ImportError`` with an actionable message.

    The operator contract: when the package is absent the error should
    name both the symptom ("lex-mcp-local is not installed") and the
    exact recovery command ("lex setup-with-ai"). A bare ``ModuleNotFoundError``
    with no guidance would leave customers stuck.
    """

    def setUp(self):
        self._restore = {}
        for key in ("lex_mcp", "lex_mcp.ai_worktree", "lex.tools.ai_worktree"):
            self._restore[key] = sys.modules.pop(key, None)

    def tearDown(self):
        for key in ("lex_mcp", "lex_mcp.ai_worktree", "lex.tools.ai_worktree"):
            sys.modules.pop(key, None)
        for key, value in self._restore.items():
            if value is not None:
                sys.modules[key] = value

    def test_1_223_import_without_lex_mcp_raises_import_error(self):
        """Scenario 1.223: importing the shim without ``lex_mcp`` installed
        raises ``ImportError``.

        Given ``lex_mcp`` is absent from the environment,
        When ``lex.tools.ai_worktree`` is imported,
        Then ``ImportError`` is raised — not ``AttributeError`` or
        ``ModuleNotFoundError`` from an unrelated layer.
        """
        # lex_mcp is genuinely not present (setUp removed it).
        with self.assertRaises(ImportError):
            importlib.import_module("lex.tools.ai_worktree")

    def test_1_224_error_message_names_recovery_command(self):
        """Scenario 1.224: the ``ImportError`` message tells the operator
        exactly what to run.

        Given ``lex_mcp`` is absent,
        When the shim is imported,
        Then the error message contains "lex setup-with-ai" so the
        operator knows the recovery path without reading the docs.
        """
        try:
            importlib.import_module("lex.tools.ai_worktree")
            self.fail("ImportError was not raised")
        except ImportError as exc:
            self.assertIn(
                "lex setup-with-ai",
                str(exc),
                f"ImportError message {str(exc)!r} should name the recovery "
                "command 'lex setup-with-ai'",
            )


# ---------------------------------------------------------------------------
# 1.225 — Attribute delegation
# ---------------------------------------------------------------------------

class TestCluster01ab_AttributeDelegation(_ShimTestBase):
    """``__getattr__`` on the shim module forwards to the real module.

    The customer contract: any name that ``lex_mcp.ai_worktree`` exposes
    must be reachable via ``lex.tools.ai_worktree.<name>``. A regression
    that returned ``None`` or raised ``AttributeError`` for a valid name
    would silently break downstream code that imports through the shim.
    """

    def test_1_225_getattr_resolves_known_name(self):
        """Scenario 1.225: ``getattr`` on the shim returns the real object.

        Given ``lex_mcp.ai_worktree`` exports ``launch_ai_worktree``,
        When ``lex.tools.ai_worktree.launch_ai_worktree`` is accessed,
        Then the returned object is the same as the one on the real module
        — not a copy, not ``None``.
        """
        import lex.tools.ai_worktree as shim

        self.assertIs(
            shim.launch_ai_worktree,
            self._ai_worktree_mod.launch_ai_worktree,
            "shim __getattr__ must return the same object as the real module",
        )

    def test_1_225b_getattr_resolves_constant(self):
        """Scenario 1.225b: scalar constants are forwarded without copying.

        Given ``lex_mcp.ai_worktree`` exports ``WORKTREE_VERSION``,
        When accessed via the shim,
        Then the value equals the one on the real module.
        """
        import lex.tools.ai_worktree as shim

        self.assertEqual(shim.WORKTREE_VERSION, self._ai_worktree_mod.WORKTREE_VERSION)

    def test_1_225c_getattr_unknown_name_raises_attribute_error(self):
        """Scenario 1.225c: unknown attribute raises ``AttributeError``.

        Given ``lex_mcp.ai_worktree`` does not have ``totally_unknown``,
        When that name is accessed on the shim,
        Then ``AttributeError`` is raised — not ``None``, which would
        mask typos until runtime.
        """
        import lex.tools.ai_worktree as shim

        with self.assertRaises(AttributeError):
            _ = shim.totally_unknown_name_xyz


# ---------------------------------------------------------------------------
# 1.226 — Directory delegation
# ---------------------------------------------------------------------------

class TestCluster01ab_DirectoryDelegation(_ShimTestBase):
    """``dir(shim)`` reflects the real module's public surface.

    The customer contract: introspection tools (IDEs, REPLs, tab-completion)
    must see what ``lex_mcp.ai_worktree`` advertises — not an empty or
    partial list that hides the actual API.
    """

    def test_1_226_dir_matches_real_module(self):
        """Scenario 1.226: ``dir()`` on the shim returns what the real
        module's ``dir()`` returns.

        Given the real module exposes ``launch_ai_worktree``,
        When ``dir(lex.tools.ai_worktree)`` is called,
        Then the result contains every name ``dir(lex_mcp.ai_worktree)``
        contains — the shim does not hide any public surface.
        """
        import lex.tools.ai_worktree as shim

        shim_dir = dir(shim)
        real_dir = dir(self._ai_worktree_mod)

        for name in real_dir:
            self.assertIn(
                name,
                shim_dir,
                f"dir() on shim is missing {name!r} that "
                "the real module exposes",
            )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
