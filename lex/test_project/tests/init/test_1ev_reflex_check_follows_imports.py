"""`lex reflex check` follows the project's own imports from its dashboards.

Intent: the check read only `_reflex_structure.py` and `_dashboards/`, so a state or a page a project
had moved into a module of its own -- imported from those files -- was never checked: a public method
the browser can call without a lex.ui decorator passed unnoticed. The check now follows every import
of the project's own modules from those files, transitively (absolute and relative imports), and
leaves lex-app's, Reflex's and other installed packages alone; the files are parsed, never run. A
regression is a broken handler in an imported module reported clean, or a check that reads
installed packages.

Cluster 1ev -- scenario 1.1031. Type: U.
Covers: lex/lex_app/reflex/ui/checks.py (followed, _imported, _module_files), lex/lex_app/reflex/scaffold.py
(check_paths).
Run: python -m lex pytest lex/test_project/tests/init/test_1ev_reflex_check_follows_imports.py -v
"""

from __future__ import annotations

import tempfile
import textwrap
from pathlib import Path

import pytest
from django.test import SimpleTestCase

from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.ui import checks

pytestmark = pytest.mark.init

BROKEN_STATE = """
from lex import ui


class TicketsState(ui.State):
    count: int = 0

    def approve(self):          # public, no lex.ui decorator: the browser can call it
        self.count += 1
"""


class TestCluster01ev_CheckFollowsImports(SimpleTestCase):
    """1.1031: the project's imported modules are checked."""

    # -- 1.1031 --------------------------------------------------------
    def test_1_1031_the_check_follows_the_projects_imports(self) -> None:
        """
        Scenario 1.1031: a state moved into a module the dashboards import.
        Given: a project whose `_reflex_structure.py` imports `orders.pages` (a package) and whose
               `_dashboards/home.py` imports `helpers`; `orders/pages/__init__.py` imports
               `.tickets` relatively, which holds a ui.State with an undecorated public method;
               `helpers.py` imports `os` and `reflex`; a module nobody imports.
        When:  `lex reflex check`'s paths are gathered and checked.
        Then:  the paths hold the two roots, the package, the relatively imported module and
               `helpers.py`, and no file outside the project nor the module nobody imports; the
               finding in `tickets.py` is reported as an error.
        """
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            files = {
                "_reflex_structure.py": "from orders.pages import tickets_page\n\ndef main():\n    return None\n",
                "_dashboards/__init__.py": "",
                "_dashboards/home.py": "import helpers\n",
                "orders/__init__.py": "",
                "orders/pages/__init__.py": "from .tickets import TicketsState\n\ntickets_page = None\n",
                "orders/pages/tickets.py": BROKEN_STATE,
                "helpers.py": "import os\nimport reflex as rx\nfrom lex import ui\n",
                "unused.py": BROKEN_STATE,
            }
            for name, text in files.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(textwrap.dedent(text), encoding="utf-8")
            paths = scaffold.check_paths(root)
            relative = sorted(str(Path(p).resolve().relative_to(root.resolve())) for p in paths)
            self.assertEqual(relative, ["_dashboards", "_reflex_structure.py", "helpers.py", "orders/__init__.py",
                                        "orders/pages/__init__.py", "orders/pages/tickets.py"],
                             "the roots and the project's modules they import, nothing else")
            findings = checks.find(paths)
        errors = [(Path(f.path).name, f.message) for f in findings if f.level == "error"]
        self.assertEqual(len(errors), 1, errors)
        self.assertEqual(errors[0][0], "tickets.py")
        self.assertIn("TicketsState.approve: a public method with no lex.ui decorator", errors[0][1])
