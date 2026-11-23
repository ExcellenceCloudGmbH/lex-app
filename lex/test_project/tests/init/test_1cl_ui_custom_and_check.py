"""lex.ui: the custom region is marked, and the check lists what leaves the default path.

Intent: raw Reflex stays possible, but only where it is marked -- @ui.custom -- so a reviewer
sees every place a page leaves lex-app's vocabulary. `lex reflex check` fails on an unmarked raw
handler on a ui.State, lists the marked ones and every requires=ui.signed_in, and warns about a
handler that names a model manager without declaring what it requires. A regression is raw
Reflex creeping back into project code unnoticed.

Cluster 1cl -- scenarios 1.781-1.782. Type: U.
Covers: lex/lex_app/reflex/ui/handlers.py (custom), lex/lex_app/reflex/ui/checks.py,
lex/lex_app/reflex/scaffold.py (run_check).
Run: python -m lex pytest lex/test_project/tests/init/test_1cl_ui_custom_and_check.py -v
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.lex_app.reflex import scaffold
from lex.lex_app.reflex.ui import checks, handlers
from lex.lex_app.reflex.ui.state import State

pytestmark = pytest.mark.init

_SOURCE = '''
import reflex as rx
from lex import ui


class Tickets(ui.State):
    rows: list[dict] = []

    @ui.action
    def approve(self, code: str):
        self.api.update("ticket", code, {"status": "approved"})

    @rx.event
    def raw(self):
        pass

    @ui.custom
    async def upload(self, files: list[rx.UploadFile]):
        pass

    @ui.action(requires=ui.signed_in)
    def anyone(self):
        pass

    @ui.job
    def totals(self):
        self.rows = list(Ticket.objects.values("id"))

    @ui.load(requires=ui.read(Ticket))
    def open(self):
        self.rows = list(Ticket.objects.values("id"))


class Plain(rx.State):
    @rx.event
    def fine(self):
        pass
'''


class TestCluster01cl_CustomAndCheck(TestCase):
    """1.781-1.782."""

    # -- 1.781 ---------------------------------------------------------
    def test_1_781_ui_custom_is_a_marked_reflex_handler(self) -> None:
        """
        Scenario 1.781: the custom region.
        Given: an async handler under @ui.custom on a ui.State.
        When:  the class is created.
        Then:  it is an ordinary Reflex event handler -- async allowed, background on request --
               that carries the custom mark.
        """
        class Uploads(State):
            @handlers.custom
            async def take(self):
                pass

            @handlers.custom(background=True)
            async def stream(self):
                pass

        self.assertEqual(Uploads.take.fn.__ui_kind__, "custom", "the handler carries the custom mark")
        self.assertFalse(Uploads.take.is_background, "a plain custom handler is not a background task")
        self.assertTrue(Uploads.stream.is_background, "background=True is passed on to Reflex")

    # -- 1.782 ---------------------------------------------------------
    def test_1_782_the_check_fails_on_unmarked_raw_handlers_and_lists_the_rest(self) -> None:
        """
        Scenario 1.782: lex reflex check.
        Given: a project file with a ui.State holding a raw @rx.event, a @ui.custom, a
               requires=ui.signed_in, a handler naming a model manager without requires=, one
               with requires=, and a plain rx.State.
        When:  the findings are collected and reported, and run_check runs over the project.
        Then:  the raw handler is an error; the custom handler and the signed_in requirement
               are notes; the unguarded manager is a warning; the guarded one and the plain
               state raise nothing; the report returns 1, and run_check returns non-zero even
               when RFDS's own checks pass. Undecorated public methods of a ui.State are errors
               (helpers, properties, static and class methods and computed vars are not);
               handlers are recognised through import aliases; a state inheriting a ui state
               from another file is checked; a file that cannot be analysed is one error naming
               it; a field, computed var or method named like a body helper (user, api, params,
               can, state) is an error; self.<handler>() of the state's own or an inherited
               handler, from any method, is an error that says to use an underscore method;
               run_check returns 0 when only warnings and notes are found.
        """
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "_reflex_structure.py"
            path.write_text(_SOURCE)
            found = checks.find([str(path)])
            by_level = {level: sorted(f.message.split(":")[0] for f in found if f.level == level)
                        for level in ("error", "warning", "note")}
            self.assertEqual(by_level["error"], ["Tickets.raw"], "an unmarked raw handler fails the check")
            self.assertEqual(by_level["warning"], ["Tickets.totals"], "a model manager without requires= is flagged")
            self.assertEqual(by_level["note"], ["Tickets.anyone", "Tickets.upload"],
                             "custom handlers and signed_in requirements are listed")
            self.assertTrue(all(f.line > 0 and f.path == str(path) for f in found), "each finding has its place")
            self.assertEqual(checks.report(found), 1, "an error makes the report return 1")
            self.assertEqual(checks.report([f for f in found if f.level != "error"]), 0, "notes and warnings pass")
            with patch("rfds.cli.main", return_value=0):
                self.assertNotEqual(scaffold.run_check(Path(folder)), 0, "the lex.ui error fails lex reflex check")

            # -- undecorated public methods, helpers and non-handlers ----------
            bare = Path(folder) / "bare.py"
            bare.write_text(
                "import functools\n"
                "import reflex as rx\n"
                "from lex import ui\n\n\n"
                "class Bare(ui.State):\n"
                "    def refresh(self):\n        pass\n\n"
                "    async def refresh_async(self):\n        pass\n\n"
                "    def _helper(self):\n        pass\n\n"
                "    def __init_subclass__(cls, **kw):\n        pass\n\n"
                "    @property\n    def prop(self):\n        return 1\n\n"
                "    @staticmethod\n    def static():\n        pass\n\n"
                "    @classmethod\n    def klass(cls):\n        pass\n\n"
                "    @rx.var\n    def computed(self) -> int:\n        return 1\n\n"
                "    @rx.var(cache=True)\n    def computed2(self) -> int:\n        return 1\n\n"
                "    @functools.cached_property\n    def cached(self):\n        return 1\n\n\n"
                "class NotUi(rx.State):\n    def public(self):\n        pass\n\n\n"
                "class Helper:\n    def public(self):\n        pass\n"
            )
            found = checks.find([str(bare)])
            self.assertEqual(sorted(f.message.split(":")[0] for f in found if f.level == "error"),
                             ["Bare.refresh", "Bare.refresh_async"],
                             "only undecorated public methods of a ui.State are errors")
            self.assertEqual([f for f in found if f.level != "error"], [], "helpers and non-handlers raise nothing")

            # -- aliases ------------------------------------------------------
            alias = Path(folder) / "alias.py"
            alias.write_text(
                "import reflex\n"
                "from reflex import event\n"
                "from lex import ui as u\n"
                "from lex.ui import State, action, signed_in\n\n\n"
                "class A(u.State):\n"
                "    @event\n    def one(self):\n        pass\n\n"
                "    @reflex.event(background=True)\n    async def two(self):\n        pass\n\n\n"
                "class B(State):\n"
                "    @action\n    def clean(self):\n        pass\n\n"
                "    @action(requires=signed_in)\n    def anyone(self):\n        pass\n\n"
                "    @reflex.event\n    def raw(self):\n        pass\n"
            )
            found = checks.find([str(alias)])
            self.assertEqual(sorted(f.message.split(":")[0] for f in found if f.level == "error"),
                             ["A.one", "A.two", "B.raw"], "raw handlers are found through import aliases")
            self.assertEqual(sorted(f.message.split(":")[0] for f in found if f.level == "note"),
                             ["B.anyone"], "requires=signed_in is recognised through an imported name")
            self.assertEqual([f for f in found if f.level == "warning"], [], "a clean @action raises nothing")

            # -- inheritance across files, and a file that does not parse ----
            base = Path(folder) / "base.py"
            base.write_text("from lex import ui\n\n\nclass Base(ui.State):\n    pass\n")
            child = Path(folder) / "child.py"
            child.write_text("import reflex as rx\nfrom pages import base\n\n\nclass Orders(base.Base):\n"
                             "    @rx.event\n    def raw(self):\n        pass\n")
            broken = Path(folder) / "broken.py"
            broken.write_text("def oops(:\n")
            found = checks.find([str(base), str(child), str(broken)])
            self.assertEqual([f.message.split(":")[0] for f in found if f.path == str(child) and f.level == "error"],
                             ["Orders.raw"], "a state inheriting a ui state from another file is checked")
            broken_findings = [f for f in found if f.path == str(broken)]
            self.assertEqual([f.level for f in broken_findings], ["error"], "a file that does not parse is one error")
            self.assertIn("broken.py", broken_findings[0].message, "the finding names the file")

        # -- reserved names, and a handler calling a sibling handler --------
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder) / "parent.py"
            parent.write_text("from lex import ui\n\n\nclass Shared(ui.State):\n"
                              "    @ui.load\n    def reload(self):\n        pass\n")
            reserved = Path(folder) / "reserved.py"
            reserved.write_text(
                "import reflex as rx\n"
                "from lex import ui\n"
                "from parent import Shared\n\n\n"
                "class Orders(Shared):\n"
                "    state: str = 'open'\n"
                "    user: str\n"
                "    api = None\n\n"
                "    @rx.var\n    def params(self) -> str:\n        return ''\n\n"
                "    def can(self):\n        pass\n\n"
                "    @ui.job\n    def export(self):\n        pass\n\n"
                "    @ui.action\n    def save(self):\n        self.reload()\n        self.export()\n"
                "        self._helper()\n\n"
                "    def _helper(self):\n        self.save()\n\n"
                "    @ui.action\n    def fine(self):\n        return Orders.export\n"
            )
            found = checks.find([str(parent), str(reserved)])
            errors = sorted((f.line, f.message.split(":")[0]) for f in found if f.level == "error")
            self.assertEqual([name for _, name in errors],
                             ["Orders.state", "Orders.user", "Orders.api", "Orders.params", "Orders.can",
                              "Orders.save", "Orders.save", "Orders._helper"],
                             "reserved names and calls to sibling handlers are errors, each once")
            messages = "\n".join(f.message for f in found)
            for expected in ("'state' is reserved", "self.reload()", "self.export()", "self.save()",
                             "self._reload()", "starts with '_'"):
                self.assertIn(expected, messages, f"the findings say {expected}")
            self.assertEqual([f for f in found if f.level != "error"], [], "nothing else is reported")

        # -- run_check is clean when only warnings and notes are found ----
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "_reflex_structure.py").write_text(
                "from lex import ui\n\n\nclass S(ui.State):\n"
                "    @ui.action(requires=ui.signed_in)\n    def anyone(self):\n        pass\n\n"
                "    @ui.job\n    def totals(self):\n        return list(Ticket.objects.all())\n")
            with patch("rfds.cli.main", return_value=0):
                self.assertEqual(scaffold.run_check(Path(folder)), 0, "notes and warnings do not fail the check")
