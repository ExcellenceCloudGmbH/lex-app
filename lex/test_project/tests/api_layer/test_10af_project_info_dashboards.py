"""``api/project-info`` tells the frontend which framework draws each model's dashboards.

Intent: lex-app's record page (Analytics tab), its tables (Analytics toggle) and
its home frame a project's dashboards, Streamlit's or Reflex's. The frontend
learns which from ``api/project-info``: ``reflex_enabled``, ``reflex_home``,
and ``dashboards`` -- per model, the framework of its record and table
dashboards. A model with its own Reflex hook gets Reflex when Reflex is on,
else one with its own Streamlit hook gets Streamlit, else nothing changes for
it; ``LEX_DASHBOARD_SURFACE=streamlit`` keeps everyone on Streamlit. The answer
is computed in the Django process without importing Reflex. A regression here
frames the wrong dashboard -- a blank Reflex frame where a Streamlit one
worked -- or drags Reflex into every Django process.
Cluster 10af -- scenarios 10.190-10.193. Type: U (10.190: E).
Covers: lex/lex_app/dashboard_surfaces.py, lex/api/views/project_info/ProjectInfo.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10af_project_info_dashboards.py -v
"""

import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase
from rest_framework.test import APITestCase

from lex.lex_app import dashboard_surfaces as surfaces

pytestmark = pytest.mark.api_layer


def _model(name: str, **hooks):
    """A stand-in model: LexModel's default hooks unless overridden."""
    from lex.core.models.LexModel import LexModel

    attrs = {hook: classmethod(lambda cls: None) for hook in hooks}
    cls = type(name, (), {
        "_meta": SimpleNamespace(model_name=name.lower()),
        "reflex_main": LexModel.reflex_main,
        "reflex_class_main": LexModel.reflex_class_main,
        "streamlit_main": LexModel.streamlit_main,
        "streamlit_class_main": LexModel.streamlit_class_main,
    })
    for hook, value in attrs.items():
        setattr(cls, hook, value)
    return cls


BOTH = _model("Both", reflex_main=True, streamlit_main=True, reflex_class_main=True)
ONLY_STREAMLIT = _model("OnlyStreamlit", streamlit_main=True, streamlit_class_main=True)
ONLY_REFLEX = _model("OnlyReflex", reflex_main=True)
NEITHER = _model("Neither")


class TestCluster10af_TheRules(SimpleTestCase):
    """Which framework a model's dashboards get."""

    # -- 10.191 --------------------------------------------------------
    def test_10_191_reflex_wins_when_on_and_the_model_has_a_reflex_hook(self) -> None:
        """
        Scenario 10.191: one predictable rule per model and level (decision D11).
        Given: a model with both hooks, one with Streamlit's only, one with
               Reflex's only, one with neither; Reflex on or off; the surface
               setting automatic or forced to Streamlit.
        When:  the record and table surfaces are decided.
        Then:  Reflex on: a Reflex hook wins, else a Streamlit hook, else none;
               Reflex off or forced to Streamlit: only Streamlit hooks count;
               record and table are decided separately; a bad level is refused.
        """
        on = dict(reflex=True, setting="auto")
        self.assertEqual(surfaces.surface_for(BOTH, "record", **on), "reflex")
        self.assertEqual(surfaces.surface_for(BOTH, "table", **on), "reflex")
        self.assertEqual(surfaces.surface_for(ONLY_STREAMLIT, "record", **on), "streamlit")
        self.assertEqual(surfaces.surface_for(ONLY_REFLEX, "record", **on), "reflex")
        self.assertIsNone(surfaces.surface_for(ONLY_REFLEX, "table", **on))
        self.assertIsNone(surfaces.surface_for(NEITHER, "record", **on))
        for off in (dict(reflex=False, setting="auto"), dict(reflex=True, setting="streamlit")):
            with self.subTest(**off):
                self.assertEqual(surfaces.surface_for(BOTH, "record", **off), "streamlit")
                self.assertIsNone(surfaces.surface_for(ONLY_REFLEX, "record", **off))
        with self.assertRaises(ValueError):
            surfaces.surface_for(BOTH, "page", **on)
        with patch.dict(os.environ, {"LEX_DASHBOARD_SURFACE": "nonsense"}):
            self.assertEqual(surfaces.surface_setting(), "auto")
        with patch.dict(os.environ, {"IS_REFLEX_ENABLED": "true", "LEX_DASHBOARD_SURFACE": ""}):
            self.assertEqual(surfaces.dashboard_surfaces([BOTH, ONLY_STREAMLIT, NEITHER]), {
                "both": {"record": "reflex", "table": "reflex"},
                "onlystreamlit": {"record": "streamlit", "table": "streamlit"},
            }, "a model with no dashboard is left out")

    # -- 10.192 --------------------------------------------------------
    def test_10_192_the_reflex_home_needs_reflex_and_a_structure_module(self) -> None:
        """
        Scenario 10.192: the frontend's home frames Reflex only when there is one to frame.
        Given: Reflex off; Reflex on with a ``_reflex_structure`` module; on without one.
        When:  ``reflex_home_available`` is read.
        Then:  False, True, False -- found without importing the module.
        """
        with patch.dict(os.environ, {"IS_REFLEX_ENABLED": "false"}):
            self.assertFalse(surfaces.reflex_home_available())
        with patch.dict(os.environ, {"IS_REFLEX_ENABLED": "true"}), \
                patch("importlib.util.find_spec", return_value=object()) as find:
            self.assertTrue(surfaces.reflex_home_available())
        self.assertTrue(find.call_args.args[0].endswith("._reflex_structure"))
        with patch.dict(os.environ, {"IS_REFLEX_ENABLED": "true"}), \
                patch("importlib.util.find_spec", return_value=None):
            self.assertFalse(surfaces.reflex_home_available())

    # -- 10.193 --------------------------------------------------------
    def test_10_193_deciding_never_imports_reflex_into_django(self) -> None:
        """
        Scenario 10.193: the Django process stays free of Reflex.
        Given: the module that decides the surfaces, and the view that answers.
        When:  their imports are read.
        Then:  neither imports ``reflex``, ``reflex_enterprise``, ``rfds`` or
               lex-app's Reflex package, at any level.
        """
        import lex.api.views.project_info.ProjectInfo as view

        for module in (surfaces, view):
            tree = ast.parse(Path(module.__file__).read_text())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module)
            forbidden = {n for n in names if n.split(".")[0] in ("reflex", "reflex_enterprise", "rfds")
                         or n.startswith("lex.lex_app.reflex")}
            self.assertEqual(forbidden, set(), module.__name__)


class TestCluster10af_TheEndpoint(APITestCase):
    """``GET api/project-info``."""

    # -- 10.190 --------------------------------------------------------
    def test_10_190_project_info_carries_the_dashboard_fields(self) -> None:
        """
        Scenario 10.190: the frontend gets everything it needs in one call.
        Given: a signed-in user; a project with a model of each kind; Reflex on
               with a structure module; then Reflex off.
        When:  ``GET /api/project-info`` is called.
        Then:  the old fields stay; ``reflex_enabled`` and ``reflex_home`` are
               true; ``dashboards`` lists every model with a dashboard, keyed by
               its route id, with its record and table surfaces, and leaves out
               a model with none; with Reflex off, Reflex models drop to
               Streamlit or out.
        """
        from lex.lex_app.tests.auth import sign_in

        user = get_user_model().objects.create_user("info", password="x")
        client = self.client
        sign_in(self, client, user)
        models = [BOTH, ONLY_STREAMLIT, ONLY_REFLEX, NEITHER]
        # Patched by path: the module the view imports when the request arrives,
        # not this test module's own reference to it.
        env = {"IS_REFLEX_ENABLED": "true", "LEX_DASHBOARD_SURFACE": "", "LEX_SUBMODEL_NAME": "demo"}
        with patch("lex.lex_app.dashboard_surfaces.project_models", return_value=models), patch.dict(os.environ, env), \
                patch("importlib.util.find_spec", return_value=object()):
            response = client.get("/api/project-info")
        self.assertEqual(response.status_code, 200, response.content[:300])
        body = response.json()
        self.assertEqual(body["project_name"], "demo")
        self.assertIs(body["reflex_enabled"], True)
        self.assertIs(body["reflex_home"], True)
        self.assertEqual(body["dashboards"], {
            "both": {"record": "reflex", "table": "reflex"},
            "onlystreamlit": {"record": "streamlit", "table": "streamlit"},
            "onlyreflex": {"record": "reflex", "table": None},
        })
        with patch("lex.lex_app.dashboard_surfaces.project_models", return_value=models), \
                patch.dict(os.environ, {**env, "IS_REFLEX_ENABLED": "false"}):
            body = client.get("/api/project-info").json()
        self.assertIs(body["reflex_enabled"], False)
        self.assertIs(body["reflex_home"], False)
        self.assertEqual(body["dashboards"], {
            "both": {"record": "streamlit", "table": None},
            "onlystreamlit": {"record": "streamlit", "table": "streamlit"},
        })
