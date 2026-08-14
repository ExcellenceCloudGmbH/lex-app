"""Guards the MCP server against the FastMCP / MCP-SDK split.

The risk model: the MCP SDK vendored a copy of FastMCP 1.0 at
``mcp.server.fastmcp`` and removed it in SDK v2. Meanwhile ``lex-mcp-local``
pins ``fastmcp>=4``, which pulls SDK v2 into any environment that installs both
-- and `lex setup-with-ai` / `lex ai-update` install lex-mcp-local into the same
interpreter as lex-app. So the vendored path is not merely deprecated here, it is
an ImportError waiting for the next customer upgrade.

Why a test rather than a note: most of ``lex/mcp_server`` lives on an unmerged
branch, where twelve modules still import the vendored path. A note in AGENTS.md
would have to be read at exactly the right moment. This fails instead, names the
files, and says what to replace them with.

None of this needs the MCP SDK installed -- the checks read source text, so they
run in any environment and cannot be silenced by whichever version happens to be
present.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

#: ``lex/mcp_server``. Anchored on the ``lex`` package (parents[3] from
#: ``lex/tests/unit/infra/``) rather than on the repo root, so a checkout at any
#: path resolves the same.
MCP_SERVER_ROOT = Path(__file__).resolve().parents[3] / "mcp_server"

#: Import paths that do not exist in MCP SDK v2, and what replaces them.
RETIRED_IMPORTS = {
    "mcp.server.fastmcp": "fastmcp",
    "mcp.server.fastmcp.resources": "fastmcp.resources",
    "mcp.server.fastmcp.prompts": "fastmcp.prompts",
    # Renamed, and re-exported under both spellings by fastmcp.exceptions.
    "mcp.shared.exceptions.McpError": "fastmcp.exceptions.McpError",
}

#: ``FastMCP.__init__`` keywords the standalone distribution never had. The
#: 2026-07-28 protocol is sessionless by construction, so ``stateless_http`` has
#: no meaning there; the HTTP-transport options moved off the constructor.
RETIRED_CONSTRUCTOR_KWARGS = frozenset(
    {"stateless_http", "json_response", "transport_security"}
)


def _python_sources() -> list[Path]:
    if not MCP_SERVER_ROOT.is_dir():
        return []
    return sorted(
        path
        for path in MCP_SERVER_ROOT.rglob("*.py")
        if "__pycache__" not in path.parts
    )


class VendoredFastMCPImportTests(unittest.TestCase):
    """Nothing under ``lex/mcp_server`` may import the SDK's vendored FastMCP."""

    #: The failure message *is* the deliverable here -- a truncated list of
    #: offending files is not a checklist anyone can work from.
    maxDiff = None

    def test_the_directory_is_present(self) -> None:
        """A rename or a move must not turn this whole module into a no-op."""
        self.assertTrue(
            MCP_SERVER_ROOT.is_dir(),
            f"{MCP_SERVER_ROOT} is gone; retarget or delete this test rather "
            "than leaving it silently passing over nothing",
        )

    def test_no_module_imports_the_vendored_fastmcp(self) -> None:
        offenders: list[str] = []
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                elif isinstance(node, ast.Import):
                    module = node.names[0].name
                else:
                    continue
                # Longest match first, or `mcp.server.fastmcp.resources` is
                # reported against the `mcp.server.fastmcp` entry and the
                # suggested replacement names the wrong module.
                for retired in sorted(RETIRED_IMPORTS, key=len, reverse=True):
                    if module == retired or module.startswith(retired + "."):
                        offenders.append(
                            f"{path.relative_to(MCP_SERVER_ROOT.parent)}:"
                            f"{node.lineno} imports {module!r} -- use "
                            f"{RETIRED_IMPORTS[retired]!r}"
                        )
                        break

        self.assertEqual(
            [],
            offenders,
            "MCP SDK v2 removed the vendored FastMCP, and lex-mcp-local pins "
            "fastmcp>=4 into the same interpreter:\n  " + "\n  ".join(offenders),
        )

    def test_no_module_passes_a_retired_fastmcp_constructor_keyword(self) -> None:
        offenders: list[str] = []
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                target = node.func
                name = getattr(target, "id", None) or getattr(target, "attr", None)
                if name != "FastMCP":
                    continue
                for keyword in node.keywords:
                    if keyword.arg in RETIRED_CONSTRUCTOR_KWARGS:
                        offenders.append(
                            f"{path.relative_to(MCP_SERVER_ROOT.parent)}:"
                            f"{node.lineno} passes FastMCP({keyword.arg}=...)"
                        )

        self.assertEqual(
            [],
            offenders,
            "The standalone FastMCP takes none of these; the HTTP-transport "
            "options moved off the constructor and the newest protocol is "
            "sessionless by construction:\n  " + "\n  ".join(offenders),
        )

    def test_add_tool_is_not_called_with_the_vendored_signature(self) -> None:
        """``add_tool(fn, name=...)`` was FastMCP 1.0. Every standalone release
        takes a single ``Tool``, so the old call is a TypeError at import time --
        which is worse than an ImportError, because it only fires on the code
        path that registers the tool."""
        offenders: list[str] = []
        for path in _python_sources():
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                if getattr(node.func, "attr", None) != "add_tool":
                    continue
                if any(keyword.arg == "name" for keyword in node.keywords):
                    offenders.append(
                        f"{path.relative_to(MCP_SERVER_ROOT.parent)}:{node.lineno}"
                    )

        self.assertEqual(
            [],
            offenders,
            "Wrap the callable: add_tool(Tool.from_function(fn, name=..., "
            "description=...)):\n  " + "\n  ".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
