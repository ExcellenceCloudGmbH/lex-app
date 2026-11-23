"""lex.ui: one import for everything, and examples written in the new style.

Intent: a project writes `from lex import ui` and finds every name the layer has; lex-app's own
scaffold templates and reference example use it, because consultants and AI copy what they see.
A regression is a template that teaches the old plumbing again.

Cluster 1cm -- scenarios 1.783-1.785. Type: U.
Covers: lex/ui.py, lex/lex_app/reflex/scaffold.py (templates),
lex/lex_app/reflex/examples/_reflex_structure.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1cm_ui_namespace_and_examples.py -v
"""

from __future__ import annotations

import ast
import tempfile
from pathlib import Path
from unittest import TestCase

import pytest

from lex import ui
from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.data import LexApiError
from lex.lex_app.reflex.ui import checks, guard, handlers, results, runs, state, testing

pytestmark = pytest.mark.init

_EXAMPLE = Path(scaffold.__file__).parent / "examples" / "_reflex_structure.py"

#: RFDS's own async hooks (not lex.ui handlers), which keep RFDS's contract.
_RFDS_HOOKS = {"_export_rows"}


def _plumbing(source: str) -> list[str]:
    """Old-style plumbing in handler code: what the layer exists to remove."""
    found = []
    tree = ast.parse(source)
    for fn in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        if fn.name in _RFDS_HOOKS:
            continue
        if isinstance(fn, ast.AsyncFunctionDef):
            found.append(f"async def {fn.name}")
        for node in ast.walk(fn):
            if isinstance(node, ast.Await):
                found.append(f"await in {fn.name}")
            if isinstance(node, ast.Name) and node.id in ("run_orm", "lex_api"):
                found.append(f"{node.id} in {fn.name}")
    return sorted(set(found))


def _errors(findings: list) -> list[str]:
    return [f.message for f in findings if f.level == "error"]


class TestCluster01cm_Namespace(TestCase):
    """1.783-1.785."""

    # -- 1.783 ---------------------------------------------------------
    def test_1_783_from_lex_import_ui_has_every_name(self) -> None:
        """
        Scenario 1.783: the public namespace.
        Given: `from lex import ui`.
        When:  its names are compared with the implementation.
        Then:  each public name is the implementation's own object, ui.ApiError is lex-app's
               API error, and ui.testing is the test helper module.
        """
        expected = {
            "State": state.State, "load": handlers.load, "action": handlers.action, "job": handlers.job,
            "custom": handlers.custom, "goto": results.goto, "toast": results.toast, "download": results.download,
            "progress": results.progress, "Refuse": results.Refuse, "ApiError": LexApiError,
            "read": guard.read, "create": guard.create, "edit": guard.edit, "delete": guard.delete,
            "permission": guard.permission, "signed_in": guard.signed_in, "running": runs.running,
            "failed": runs.failed, "progress_of": runs.progress_of, "cancel": runs.cancel, "testing": testing,
        }
        for name, obj in expected.items():
            self.assertIs(getattr(ui, name), obj, f"ui.{name}")
        self.assertEqual(sorted(ui.__all__), sorted(expected), "nothing else is public")

    # -- 1.784 ---------------------------------------------------------
    def test_1_784_the_scaffold_templates_are_written_on_the_layer(self) -> None:
        """
        Scenario 1.784: what `lex reflex add` writes.
        Given: the three templates, rendered for a model.
        When:  their handler code is inspected and checked.
        Then:  none contains async, await, run_orm or lex_api; the object page and the overview
               use ui.State with @ui.load; the lex.ui check finds no error in them.
        """
        for block in ("list_report", "object_page", "overview"):
            _, source = scaffold.render(block, "Ticket", package="demo")
            with self.subTest(block=block):
                self.assertEqual(_plumbing(source), [], f"{block} must not teach the old plumbing")
                if block != "list_report":
                    self.assertIn("ui.State", source, f"{block}'s state is a ui.State")
                    self.assertIn("@ui.load", source, f"{block} fills its page with @ui.load")
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / f"{block}.py"
                    path.write_text(source, encoding="utf-8")
                    self.assertEqual(_errors(checks.find([str(path)])), [],
                                     f"the lex.ui check must pass what {block} writes")

    # -- 1.785 ---------------------------------------------------------
    def test_1_785_the_reference_example_is_written_on_the_layer(self) -> None:
        """
        Scenario 1.785: the example consultants copy.
        Given: lex-app's reference _reflex_structure.py.
        When:  its handler code is inspected and checked.
        Then:  outside RFDS's own async export hook it contains no async, await, run_orm or
               lex_api; every state with handlers is a ui.State; the lex.ui check reports no
               error.
        """
        source = _EXAMPLE.read_text(encoding="utf-8")
        self.assertEqual(_plumbing(source), [], "the example must not teach the old plumbing")
        with_handlers = [cls for cls in ast.walk(ast.parse(source)) if isinstance(cls, ast.ClassDef)
                         and any(isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_") for fn in cls.body)]
        self.assertTrue(with_handlers, "the example has states with handlers")
        for cls in with_handlers:
            self.assertIn("ui.State", [ast.unparse(base) for base in cls.bases], f"{cls.name} is a ui.State")
        self.assertEqual(_errors(checks.find([str(_EXAMPLE)])), [], "the lex.ui check must pass the example")
        self.assertIn("from lex import ui", source, "the example imports the layer as projects do")
