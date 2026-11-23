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
import asyncio
import contextlib
import io
import re
import sys
import tempfile
import types
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest
from django.contrib.auth.models import Group, Permission

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
            if isinstance(node, ast.Attribute) and node.attr in ("run_orm", "lex_api"):
                found.append(f"{node.attr} in {fn.name}")
    return sorted(set(found))


def _errors(findings: list) -> list[str]:
    return [f.message for f in findings if f.level == "error"]


def _non_ui_states(source: str) -> list[str]:
    """Classes with public methods (handlers, to Reflex) whose bases do not include ui.State."""
    return [cls.name for cls in ast.walk(ast.parse(source)) if isinstance(cls, ast.ClassDef)
            and any(isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_") for fn in cls.body)
            and "ui.State" not in [ast.unparse(base) for base in cls.bases]]


def _branch_order(page: str, state: str, content: str) -> list[int]:
    """Where, in a compiled view, the three branches of a load's region start.

    The failure message (its condition reads ui_failed), then the content (its condition is the
    loaded ``record`` of ``state``), then the spinner. -1 marks a missing part.
    """
    failure = page.find("ui_failed")
    message = page.find("rfds-inline-message", failure)
    loaded = re.search(r"isTrue\([^()]*" + state + r"[^()]*\.record_rx_state_\)\?", page)
    start = loaded.start() if loaded else -1
    return [failure, message, start, page.find(content, max(start, 0)), page.find("RfdsSpinnerView", max(start, 0))]


def _check_reports(paths: list[str]) -> tuple[list[str], int, str]:
    """What ``lex reflex check`` reports on ``paths``, through the entry points ``scaffold.run_check`` uses.

    Returns the lex.ui errors and warnings, RFDS's strict lint exit status, and its output.
    """
    from rfds.cli import main as rfds_main

    findings = [f"{f.level}: {f.message}" for f in checks.find(paths) if f.level in ("error", "warning")]
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        status = rfds_main(["lint", "--strict", *paths])
    return findings, status, output.getvalue()


def _components(component) -> list:
    """``component`` and every component under it."""
    found = [component]
    for child in getattr(component, "children", ()):
        found.extend(_components(child))
    return found


class _Api:
    """A stand-in for self.api that records its calls."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def get(self, model, pk):
        self.calls.append(("get", model, pk))
        return {"id": pk, "short_description": f"Record {pk}", "name": "Alpha", "tags": [3, 5]}

    def update(self, model, pk, data):
        self.calls.append(("update", model, pk, data))
        return {"id": pk, **data}


def _export_rows(example, grants: list) -> list:
    """The overview's export rows for a viewer holding ``grants``, through lex-app's own permission check."""

    async def permissions(state):
        return list(testing._grants(grants))

    async def scenario():
        async with testing.Harness(grants=grants) as harness:
            await harness.open_tab()
            overview = await harness._state(example.OverviewState)
            with patch("lex.lex_app.reflex.auth.current_permissions", permissions):
                _, rows, _ = await overview._export_rows("csv")
            return rows

    return asyncio.run(scenario())


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
        pages = {"page", "dashboard", "record", "list", "section", "text", "fact", "status", "message", "table", "column",
                 "button", "link", "each", "switch", "custom_block", "loaded", "PageError",   # batch 1cu covers these
                 "rows", "address", "refresh", "embed"}                                      # and batches 1cv, 1cy these
        self.assertEqual(sorted(set(ui.__all__) - pages), sorted(expected),
                         "nothing else is public beside the page names")

    # -- 1.784 ---------------------------------------------------------
    def test_1_784_the_scaffold_templates_are_written_on_the_layer(self) -> None:
        """
        Scenario 1.784: what `lex reflex add` writes.
        Given: the three templates, rendered for a model.
        When:  their handler code is inspected and checked.
        Then:  none contains async, await, run_orm or lex_api (as a name or an attribute); every
               state with handlers is a ui.State; the object page and the overview use
               ui.State with @ui.load; neither the lex.ui check nor RFDS's strict lint (what
               `lex reflex check` runs) reports an error or a warning on any of them; the object
               page shows its load's failure, else the loaded record, else a spinner -- so
               nothing is framed before the record has arrived -- and its load, started on
               mount, reports a refusal in place (in ui.failed, no redirect).
        """
        for block in ("list_report", "object_page", "overview"):
            _, source = scaffold.render(block, "Ticket", package="demo")
            with self.subTest(block=block):
                self.assertEqual(_plumbing(source), [], f"{block} must not teach the old plumbing")
                self.assertEqual(_non_ui_states(source), [], f"every state with handlers in {block} is a ui.State")
                if block != "list_report":
                    self.assertIn("ui.State", source, f"{block}'s state is a ui.State")
                    self.assertIn("@ui.load", source, f"{block} fills its page with @ui.load")
                with tempfile.TemporaryDirectory() as folder:
                    path = Path(folder) / f"{block}.py"
                    path.write_text(source, encoding="utf-8")
                    findings, status, output = _check_reports([str(path)])
                    self.assertEqual(findings, [], f"the lex.ui check reports nothing on what {block} writes")
                    self.assertEqual(status, 0, f"RFDS's strict lint reports nothing on what {block} writes:\n{output}")
                    self.assertIn("0 total", output, f"RFDS's strict lint finds nothing in {block}:\n{output}")

        module, source = scaffold.render("object_page", "Ticket", package="demo")
        rendered = types.ModuleType(f"lex_1cm_rendered_{module}")
        sys.modules[rendered.__name__] = rendered      # Reflex reads a state's annotations from its module
        exec(compile(source, f"{module}.py", "exec"), rendered.__dict__)
        page = str(rendered.ticket_record_page())
        order = _branch_order(page, "ticket_record_state", "/show")
        self.assertNotIn(-1, order, f"the object page shows a failure, the loaded record and a spinner: {order}")
        self.assertEqual(order, sorted(order),
                         "the object page shows its failure first, then the loaded record, otherwise a spinner")
        record_state = next(obj for obj in vars(rendered).values()
                            if isinstance(obj, type) and issubclass(obj, state.State) and obj is not state.State)
        missing = testing.run(record_state, "open")
        self.assertTrue(missing.refused, "the object page's load refuses a link that names no record")
        self.assertEqual((missing.failed.get(runs.key_of(record_state.open)), missing.goto), (missing.refused, None),
                         "its load, started on mount, reports the refusal in place: in ui.failed, no redirect")

    # -- 1.785 ---------------------------------------------------------
    def test_1_785_the_reference_example_is_written_on_the_layer(self) -> None:
        """
        Scenario 1.785: the example consultants copy.
        Given: lex-app's reference _reflex_structure.py.
        When:  its handler code is inspected and checked.
        Then:  outside RFDS's own async export hook it contains no async, await, run_orm or
               lex_api; every state with handlers is a ui.State; the lex.ui check reports no
               error, and neither it nor RFDS's strict lint reports a warning. The overview's
               load, started on mount, reports its refusal in place on `/` (in ui.failed, no
               redirect), where the export menu is offered only while the load has not failed;
               a viewer who may not read the featured model is refused by the load (no
               redirect) and by the export; a viewer who may read some models sees the same
               counts on screen and in the export, only for those models. The record
               dashboard reads the model `?model=` names and refuses a missing or unknown
               one -- in ui.failed, with no redirect -- and shows failure, else the loaded
               record, else a spinner. The edit page
               is a form submitted to one action: no per-keystroke handler, and `save` writes
               the submitted values.
        """
        from lex.lex_app.reflex.examples import _reflex_structure as example
        from lex.test_project.tests.init._reflex_compiled import example_model

        source = _EXAMPLE.read_text(encoding="utf-8")
        self.assertEqual(_plumbing(source), [], "the example must not teach the old plumbing")
        with_handlers = [cls for cls in ast.walk(ast.parse(source)) if isinstance(cls, ast.ClassDef)
                         and any(isinstance(fn, ast.FunctionDef) and not fn.name.startswith("_") for fn in cls.body)]
        self.assertTrue(with_handlers, "the example has states with handlers")
        for cls in with_handlers:
            self.assertIn("ui.State", [ast.unparse(base) for base in cls.bases], f"{cls.name} is a ui.State")
        self.assertEqual(_errors(checks.find([str(_EXAMPLE)])), [], "the lex.ui check must pass the example")
        self.assertIn("from lex import ui", source, "the example imports the layer as projects do")
        findings, status, output = _check_reports([str(_EXAMPLE)])
        self.assertEqual(findings, [], "the lex.ui check reports no error or warning on the example")
        self.assertEqual(status, 0, f"RFDS's strict lint reports nothing on the example:\n{output}")
        self.assertIn("0 total", output, f"RFDS's strict lint finds nothing in the example:\n{output}")

        with patch.object(example, "featured", lambda: Group), \
                patch.object(example, "_project_models", lambda: [Group, Permission]):
            # -- the overview: started on mount, refused in place, the export agreeing with the screen
            home = str(example.main())
            failure = home.find("ui_failed")
            self.assertGreater(failure, -1, "main() reads the overview load's failure")
            self.assertGreater(home.find("rfds-inline-message", failure), -1, "and shows it in an inline message")
            menu = home.find("rfds-export-menu")
            condition = home[home.rfind("jsx(Fragment,{},(", 0, menu):menu]
            self.assertIn("ui_failed", condition, "the export menu is shown under a condition on the load's failure")
            self.assertFalse(condition.startswith("jsx(Fragment,{},(!("),
                             "the export menu is shown when the load did not fail, not when it did")

            refused = testing.run(example.OverviewState, "open")
            self.assertTrue(refused.refused, "a viewer who may not read the featured model is refused")
            self.assertIsNone(refused.goto, "the refusal stays on the page")
            self.assertEqual(refused.fields["ui_error"], {}, "no error page is set up")
            self.assertEqual(refused.failed.get(runs.key_of(example.OverviewState.open)), refused.refused,
                             "the refusal is in ui.failed: a refusal must not take the home page away")
            with self.assertRaises(ui.Refuse, msg="the export refuses exactly when the load refuses"):
                _export_rows(example, [])

            some = [(Group, "read")]
            screen = testing.run(example.OverviewState, "open", grants=some)
            self.assertEqual(screen.outcomes, [], "a viewer who may read the featured model sees the figures")
            self.assertEqual([row["model"] for row in screen.fields["counts"]], ["Groups"],
                             "only the models the viewer may read are counted")
            self.assertEqual(_export_rows(example, some), screen.fields["counts"],
                             "the export holds exactly what the screen shows")

            # -- the record dashboard: the model ?model= names
            fund = example_model()
            api = _Api()
            with patch("lex.lex_app.reflex.dashboards._project_app_label", return_value="lex_app"):
                read = testing.run(example.RecordState, "open", api=api,
                                   params={"model": fund._meta.model_name, "pk": "7"})
                self.assertEqual(read.outcomes, [], "the named record is read")
                self.assertEqual(read.fields["record"]["tags"], [3, 5],
                                 "a record keeps a field holding a list (many-to-many ids)")
                self.assertEqual(api.calls, [("get", fund, "7")], "from the model ?model= names, not the featured one")
                for params in ({"pk": "7"}, {"model": "nosuchmodel", "pk": "7"}, {"model": fund._meta.model_name}):
                    with self.subTest(params=params):
                        missing = testing.run(example.RecordState, "open", api=_Api(), params=params)
                        self.assertTrue(missing.refused, "a missing or unknown model, or no pk, is refused")
                        self.assertEqual((missing.failed.get(runs.key_of(example.RecordState.open)), missing.goto),
                                         (missing.refused, None), "in place, in ui.failed, with no redirect")
            order = _branch_order(str(example.record_dashboard()), "record_state", "rfds-key-value")
            self.assertNotIn(-1, order, f"the record dashboard shows a failure, the record and a spinner: {order}")
            self.assertEqual(order, sorted(order),
                             "the record dashboard shows its failure first, then the loaded record, otherwise a spinner")

            # -- the edit page: a form, one action
            setters = [name for name in example.EditState.event_handlers if name.startswith("set_")]
            self.assertEqual(setters, [], "the edit page has no per-keystroke handler")
            edit_page = example.edit()
            self.assertNotIn("onChange", str(edit_page), "no input sends its keystrokes to the state")
            self.assertIn('ui_running_rx_state_?.["EditState.save"]', str(edit_page),
                          "the Save button shows the save in flight")
            submits = [[event.handler.fn for event in trigger.events] for component in _components(edit_page)
                       for name, trigger in component.event_triggers.items() if name == "on_submit"]
            self.assertEqual(submits, [[example.EditState.save.fn]], "the page's one form is submitted to EditState.save")
            api = _Api()
            saved = testing.run(example.EditState, "save", api=api,
                                form={"pk": "3", "field_name": "name", "value": "Beta"})
            self.assertEqual(saved.outcomes, [], "a complete form is saved")
            self.assertEqual(api.calls, [("update", Group, "3", {"name": "Beta"})], "with the submitted values")
            self.assertTrue(testing.run(example.EditState, "save", api=_Api(), form={"value": "Beta"}).refused,
                            "a form without a record and a field is refused")
