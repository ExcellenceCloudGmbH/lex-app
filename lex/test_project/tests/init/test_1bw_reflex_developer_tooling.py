"""A consultant's tooling for Reflex dashboards: ``lex reflex add``, ``lex reflex check``, the reference example.

Intent: a consultant gets a working, signed-in, permission-aware page for one of
the project's models with one command (``lex reflex add <block> --model X``),
checks a project's dashboards the way CI does with another (``lex reflex
check``), and copies a reference ``_reflex_structure.py`` that uses every
feature lex-app gives a Reflex dashboard -- and which compiles, because a test
compiles it. A regression here hands consultants a template that does not
import, a page that never registers, a check that passes what CI fails, or an
example that teaches something that no longer exists.
Cluster 1bw -- scenarios 1.724-1.727. Type: U.
Covers: lex/lex_app/reflex/scaffold.py (render, add_block, check_paths,
run_check), lex/bin/lex.py (``lex reflex add`` / ``lex reflex check``),
lex/lex_app/reflex/examples/_reflex_structure.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1bw_reflex_developer_tooling.py -v
"""

import ast
import contextlib
import os
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner
from django.test import SimpleTestCase

from lex.lex_app import reflex as lex_reflex
from lex.lex_app.reflex import scaffold

pytestmark = pytest.mark.init


class _Project(SimpleTestCase):
    """A temporary project root, with ``lex reflex`` pointed at it."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        root_patch = patch("lex.bin.lex.PROJECT_ROOT_DIR", self.root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

    def lex(self, *args: str):
        from lex.bin.lex import lex as lex_cli

        with contextlib.chdir(Path.cwd()):
            return CliRunner().invoke(lex_cli, ["reflex", *args], catch_exceptions=False)


class TestCluster01bw_Add(_Project):
    """``lex reflex add``."""

    # -- 1.724 ---------------------------------------------------------
    def test_1_724_add_writes_a_page_and_registers_it_once(self) -> None:
        """
        Scenario 1.724: one command, one registered page bound to the model.
        Given: a project without dashboards.
        When:  ``lex reflex add list_report --model CashPosition --nav Finance`` runs,
               then again, then with ``--force``; ``overview`` with ``--no-register``;
               ``object_page``; and with a bad model name, block or route.
        Then:  ``_dashboards/`` (a package Django's discovery skips) holds the
               page, ``_reflex_structure.py`` is created with a ``main()`` and
               imports it once; a second run is refused without ``--force``;
               ``--no-register`` and ``object_page`` (a model hook, not a route)
               leave the structure alone; bad input exits 2 and writes nothing.
        """
        result = self.lex("add", "list_report", "--model", "CashPosition", "--nav", "Finance")
        self.assertEqual(result.exit_code, 0, result.output)
        page = self.root / "_dashboards" / "cash_position_list_report.py"
        self.assertTrue(page.exists())
        self.assertTrue((self.root / "_dashboards" / "__init__.py").exists())
        structure = (self.root / "_reflex_structure.py").read_text()
        self.assertIn("def main():", structure)
        import_line = "from ._dashboards import cash_position_list_report"
        self.assertEqual(structure.count(import_line), 1)
        source = page.read_text()
        self.assertIn('ROUTE = "/cash-position"', source)
        self.assertIn('nav="Finance"', source)
        self.assertIn('requires=f"{repo_name}.CashPosition:read"', source)

        again = self.lex("add", "list_report", "--model", "CashPosition")
        self.assertEqual(again.exit_code, 2)
        self.assertIn("--force", again.output)
        forced = self.lex("add", "list_report", "--model", "CashPosition", "--force")
        self.assertEqual(forced.exit_code, 0, forced.output)
        self.assertEqual((self.root / "_reflex_structure.py").read_text().count(import_line), 1)

        before = (self.root / "_reflex_structure.py").read_text()
        self.assertEqual(self.lex("add", "overview", "--model", "Fund", "--no-register").exit_code, 0)
        self.assertEqual(self.lex("add", "object_page", "--model", "Fund").exit_code, 0)
        self.assertEqual((self.root / "_reflex_structure.py").read_text(), before)

        for args in (("list_report", "--model", "fund"), ("list_report", "--model", "Fund", "--route", "x")):
            with self.subTest(args=args):
                self.assertEqual(self.lex("add", *args).exit_code, 2)
        self.assertNotEqual(self.lex("add", "kanban", "--model", "Fund").exit_code, 0)

    # -- 1.725 ---------------------------------------------------------
    def test_1_725_every_template_is_valid_and_uses_only_lex_apps_surface(self) -> None:
        """
        Scenario 1.725: what ``add`` writes imports, and imports only what exists.
        Given: each block, for a two-word model.
        When:  it is rendered.
        Then:  it parses; every name it imports from ``lex.lex_app.reflex`` is
               one the package exports; list and overview pages are ``@lex_page``
               routes gated on the model's read grant; the object page is a
               ``LexRecordState`` dashboard for ``reflex_main``, whose suggested
               import names the project's package; the overview's database read
               is behind ``has_permission``.
        """
        exported = set(lex_reflex.__all__)
        for block in scaffold.BLOCKS:
            with self.subTest(block=block):
                module, source = scaffold.render(block, "CashPosition")
                self.assertEqual(module, f"cash_position_{block}")
                tree = ast.parse(source)
                imported = {alias.name for node in ast.walk(tree)
                            if isinstance(node, ast.ImportFrom) and node.module == "lex.lex_app.reflex"
                            for alias in node.names}
                self.assertTrue(imported, "a template uses lex-app's surface")
                self.assertLessEqual(imported, exported, f"imports a name lex.lex_app.reflex does not export")
        self.assertIn("@lex_page(", scaffold.render("list_report", "CashPosition")[1])
        record = scaffold.render("object_page", "CashPosition", package="crm")[1]
        self.assertIn("class CashPositionRecordState(LexRecordState)", record)
        self.assertNotIn("@lex_page", record)
        self.assertIn("from crm._dashboards.cash_position_object_page import", record,
                      "the hook imports the project's package by name, never relatively")
        self.assertNotIn("Imported by _reflex_structure.py", record, "an object page is never registered")
        for state in ('"ok"', '"denied"', '"missing"', '"unavailable"'):
            self.assertIn(state, record, f"the record page handles record_state {state}")
        self.assertIn("auth=has_permission(", scaffold.render("overview", "CashPosition")[1])


class TestCluster01bw_Check(_Project):
    """``lex reflex check``."""

    # -- 1.726 ---------------------------------------------------------
    def test_1_726_check_lints_the_dashboards_and_runs_the_doctor(self) -> None:
        """
        Scenario 1.726: the check CI runs, from the project root.
        Given: a project with a structure module and a dashboards package; then
               RFDS's lint failing; then a project with neither.
        When:  ``lex reflex check`` runs.
        Then:  RFDS lints exactly the structure module and the package, strictly,
               then runs ``doctor`` on the project; the command fails when
               either fails; with nothing to lint, only the doctor runs.
        """
        (self.root / "_reflex_structure.py").write_text("def main():\n    pass\n")
        (self.root / "_dashboards").mkdir()
        calls: list[list[str]] = []

        def rfds_main(argv):
            calls.append(list(argv))
            return 0

        with patch("rfds.cli.main", rfds_main):
            result = self.lex("check")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(calls, [
            ["lint", "--strict", str(self.root / "_reflex_structure.py"), str(self.root / "_dashboards")],
            ["doctor", "--app-root", str(self.root)],
        ])
        with patch("rfds.cli.main", lambda argv: 1 if argv[0] == "lint" else 0):
            self.assertEqual(self.lex("check").exit_code, 1)
        calls.clear()
        (self.root / "_reflex_structure.py").unlink()
        (self.root / "_dashboards").rmdir()
        with patch("rfds.cli.main", rfds_main):
            self.assertEqual(self.lex("check").exit_code, 0)
        self.assertEqual([c[0] for c in calls], ["doctor"])


class TestCluster01bw_TheExample(SimpleTestCase):
    """The reference ``_reflex_structure.py``."""

    # -- 1.727 ---------------------------------------------------------
    def test_1_727_the_reference_example_uses_every_feature_and_compiles(self) -> None:
        """
        Scenario 1.727: the example a consultant copies is the whole surface, and it compiles.
        Given: ``examples/_reflex_structure.py``.
        When:  it is read, and compiled in the app as ``reflex run`` compiles it.
        Then:  it imports every feature lex-app gives a dashboard (pages, chrome
               permissions, tables, grid, records, the API, lex_view with flows,
               widgets, the ORM bridge); its pages are in the compiled app under
               one navigation heading; and the compile passed RFDS's
               conformance checks in their strict CI mode.
        """
        from lex.lex_app.reflex import shell
        from lex.lex_app.reflex.examples import _reflex_structure as example
        from lex.test_project.tests.init._reflex_compiled import compiled_app

        source = Path(example.__file__).read_text()
        tree = ast.parse(source)
        imported = {alias.name for node in ast.walk(tree)
                    if isinstance(node, ast.ImportFrom) and node.module == "lex.lex_app.reflex"
                    for alias in node.names}
        for feature in ("lex_page", "lex_table", "lex_grid", "LexRecordState", "lex_api", "lex_view", "Flow",
                        "lex_widgets", "calculation", "calculation_log", "has_permission", "permission",
                        "run_orm", "current_permissions", "LexUser"):
            self.assertIn(feature, imported, f"the example shows {feature}")
        for used in ("ExportMixin", "export_menu", "rfds.bar_chart", "rfds.kpi_tile", "can("):
            self.assertIn(used, source)

        compiled = compiled_app()
        for route in ("records", "grid", "in-lex-app", "calculations", "edit"):
            self.assertIn(route, compiled.pages)
        with patch.dict(os.environ, {"CI": "true", "LEX_RFDS_CONFORMANCE": ""}):
            from lex.lex_app.reflex.design import conformance_level

            self.assertEqual(conformance_level(), "error", "the compile above ran RFDS's checks strictly")
        self.assertIn("Example", {entry.section for entry in shell.nav_entries()} | {example.NAV})
