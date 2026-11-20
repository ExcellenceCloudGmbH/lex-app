"""Cluster 1aq: Reflex dashboards at run time -- Keycloak sign-in, the Django
ORM from event handlers, and the ``?model=&pk=`` dispatch.

Intent
------
A Reflex dashboard must give its author what a Streamlit dashboard gives:

* **Sign-in through lex-app's own**, with nothing new to configure and no
  Reflex account: plain Reflex and RFDS, the dashboard hand-off of
  ``lex.lex_app.reflex.session`` (batch 1bx), and ``LexUser`` naming the
  signed-in user from it. (1.358 and 1.359 tested Reflex Enterprise's
  Keycloak provider; they retired with it.)
* **The Django ORM.** Reflex runs handlers on its event loop, where Django
  refuses synchronous queries; ``run_orm`` / ``@orm`` hand synchronous Django
  code to Django's own executor, and every event gets the connection handling
  a Django request gets, so one dropped connection cannot fail every later
  query.
* **The same URL contract** -- ``?model=fund&pk=42`` is the record's dashboard,
  ``?model=fund`` the table's, neither the project's own -- with Streamlit's
  error messages for an unknown model, a missing record, or a model that cannot
  draw one.
* **The same place in lex-app**: a ``Reflex`` report and sidebar entry, only
  when ``IS_REFLEX_ENABLED`` says so; Django discovery that never imports the
  Reflex package under a second name (which would define every state twice);
  and a Django setup that survives the uvloop loop granian runs.

Cluster 1aq -- scenarios 1.357-1.369 (1.358-1.359 retired). Type: U / I.
Covers: lex/lex_app/reflex/ (config.py, auth.py ``LexUser``, django_orm.py, dashboards.py, app.py, Reflex.py,
examples/_reflex_structure.py), lex/reflex_app.py,
lex/utilities/config/generic_app_config.py, lex/lex_app/apps.py
(``ready()``, ``register_models``),
lex/process_admin/utils/model_structure_builder.py, lex/core/models/LexModel.py
(``reflex_main`` / ``reflex_class_main``).
Run: python -m lex pytest lex/test_project/tests/init/test_1aq_reflex_dashboards.py -v
"""

from __future__ import annotations

import contextlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex as rx
from asgiref.sync import async_to_sync
from django.core.exceptions import SynchronousOnlyOperation
from django.db import connection, models, transaction

from lex.core.models.LexModel import LexModel
from lex.lex_app.reflex.auth import LexUser
from lex.lex_app.reflex.dashboards import LexDashboardState
from lex.test_project.tests._e2e_test_case import E2ETestCase


def _child_env() -> dict:
    """The environment for a child interpreter that must import the lex under test.

    The child runs elsewhere (a temporary directory), so the checkout the parent imported
    ``lex`` from is not on its path unless lex-app is installed (as in CI): put it there.
    """
    import lex

    env = dict(os.environ)
    checkout = str(Path(lex.__file__).resolve().parent.parent)
    env["PYTHONPATH"] = os.pathsep.join(p for p in (checkout, env.get("PYTHONPATH")) if p)
    return env

pytestmark = pytest.mark.init


class ReflexProbeFund(LexModel):
    """A model with both Reflex dashboards."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

    @classmethod
    def reflex_main(cls):
        return rx.text("probe record dashboard")

    @classmethod
    def reflex_class_main(cls):
        return rx.text("probe table dashboard")


class ReflexProbeDefault(LexModel):
    """A LexModel that overrides neither hook."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class ReflexProbePlain(models.Model):
    """A plain Django model: no dashboard hooks at all."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


#: The project's app is where dispatch looks models up. This test project has
#: none of its own, so the probes live in lex_app and dispatch looks there.
_PROJECT_APP = patch("lex.lex_app.reflex.dashboards._project_app_label", return_value="lex_app")

def _root_state(url: str = "http://localhost:8502/", binding: str = ""):
    """A root Reflex state whose router is on ``url``, the way an event sees it.

    ``binding``: the ``X-Lex-Binding`` the socket guard put on the session's socket.
    """
    from reflex.istate.data import HeaderData, ReflexURL, RouterData
    from reflex.state import State

    headers = {"host": "localhost:8503", "origin": "http://localhost:8502"}
    if binding:
        headers["x-lex-binding"] = binding
    root = State()
    root.router = RouterData(url=ReflexURL(url), headers=HeaderData.from_router_data({"headers": headers}))
    return root


def _substate(root, state_cls):
    return root.get_substate(state_cls.get_full_name().split("."))


class TestCluster01aq_Config(TestCase):
    """What rxconfig.py returns: lex-app's app module, sign-in, no ports."""

    # -- 1.357 ---------------------------------------------------------
    def test_1_357_lex_config_wires_the_app_module_sign_in_and_defaults(self) -> None:
        """
        Scenario 1.357: ``lex_config()`` is a plain rx.Config wired for Lex App.
        Given: no overrides.
        When:  ``lex_config()`` builds the config.
        Then:  it is Reflex's own Config (no Reflex Enterprise, so no Reflex
               account for any run mode); Reflex imports ``lex.reflex_app``;
               telemetry is off; its plugins are Radix and RFDS only -- sign-in
               is lex-app's own, added by the app, not a plugin; the sitemap is
               off; no port is configured (a configured port breaks
               ``--backend-only`` and ``--env prod``); an override wins.
        """
        from reflex.config import Config

        from lex.lex_app.reflex.config import lex_config

        config = lex_config()
        self.assertIs(type(config), Config, "a plain rx.Config, not Reflex Enterprise's")
        self.assertEqual(config.app_module_import, "lex.reflex_app")
        self.assertFalse(config.telemetry_enabled)
        self.assertIsNone(config.frontend_port, "a configured port breaks --backend-only")
        self.assertIsNone(config.backend_port, "two configured ports break --env prod")
        self.assertEqual([type(p).__name__ for p in config.plugins], ["RadixThemesPlugin", "RFDSPlugin"])
        self.assertIn(rx.plugins.SitemapPlugin, config.disable_plugins)
        self.assertFalse(config.show_built_with_reflex)
        self.assertTrue(lex_config(telemetry_enabled=True).telemetry_enabled, "an override must win")
        self.assertNotIn("reflex_enterprise", sys.modules, "nothing here may import Reflex Enterprise")

    def test_1_357b_app_names_are_valid_reflex_identifiers(self) -> None:
        """
        Scenario 1.357: the app name Reflex requires is derived from the project.
        Given: project directory names Reflex would reject.
        When:  ``app_name_for`` derives the name.
        Then:  it is an identifier starting with a letter, never ``reflex``.
        """
        from lex.lex_app.reflex.config import app_name_for

        self.assertEqual(app_name_for("/p/Northwind"), "Northwind")
        self.assertEqual(app_name_for("/p/my-project"), "my_project")
        self.assertEqual(app_name_for("/p/1st"), "lex_1st")
        self.assertEqual(app_name_for("/p/reflex"), "reflex_dashboards")

    def test_1_357c_the_config_module_creates_no_state(self) -> None:
        """
        Scenario 1.357: importing the config -- what rxconfig.py does -- creates no Reflex state.
        Given: a fresh interpreter.
        When:  ``lex.lex_app.reflex`` and its ``config`` module are imported.
        Then:  neither the auth provider nor any dashboard state has been
               defined: a provider created while rxconfig.py loads re-enters
               the config it is part of. Every lazy export resolves, and
               ``__all__`` names exactly those.
        """
        code = (
            "import sys\n"
            "import lex.lex_app.reflex, lex.lex_app.reflex.config\n"
            "print(sorted(m for m in sys.modules if m.startswith('lex.lex_app.reflex.')))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
            cwd=tempfile.gettempdir(),
            env=_child_env(),
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
        loaded = result.stdout.strip().splitlines()[-1]
        self.assertEqual(loaded, "['lex.lex_app.reflex.config']", f"eagerly imported: {loaded}")

        # The lazy exports, and the literal `__all__` tools read them from without
        # importing the package (the docs gate among them), are one list.
        import lex.lex_app.reflex as package

        self.assertEqual(sorted(package.__all__), sorted(package._EXPORTS))
        for name in package.__all__:
            with self.subTest(name=name):
                source = importlib.import_module(package._EXPORTS[name])
                self.assertIs(getattr(package, name), getattr(source, name))


class TestCluster01aq_User(TestCase):
    """The signed-in user's names, as frontend vars."""

    # -- 1.369 ---------------------------------------------------------
    def test_1_369_lex_user_projects_the_names_lex_app_shows(self) -> None:
        """
        Scenario 1.369: ``LexUser`` names the signed-in user from the session's binding.
        Given: a session whose binding's sign-in has a name, only a username, only
               an e-mail; and one with no binding.
        When:  ``LexUser``'s vars are read.
        Then:  ``username`` is ``preferred_username``, ``email`` the address;
               ``display_name`` is the name, else the username, else the email
               -- all empty without a sign-in.
        """
        import time

        from lex.lex_app.reflex import session

        cases = [
            ({"sub": "1", "name": "Ada Lovelace", "preferred_username": "ada", "email": "a@x"},
             "ada", "Ada Lovelace"),
            ({"sub": "1", "name": "", "preferred_username": "ada", "email": "a@x"}, "ada", "ada"),
            ({"sub": "1", "name": "", "preferred_username": "", "email": "a@x"}, "", "a@x"),
            ({}, "", ""),
        ]
        digest = "e" * 64
        self.addCleanup(session.forget)
        for profile, username, display_name in cases:
            with self.subTest(profile=profile):
                session.forget()
                if profile:
                    who = SimpleNamespace(sub=profile["sub"], issuer="kc", email=profile["email"])
                    session._remember(digest, session.Standing(time.monotonic(), 7, who, profile))
                root = _root_state(binding=digest)
                user = _substate(root, LexUser)
                self.assertEqual(user.username, username)
                self.assertEqual(user.email, profile.get("email", ""))
                self.assertEqual(user.display_name, display_name)


class TestCluster01aq_Orm(E2ETestCase):
    """The Django ORM from Reflex's event loop."""

    e2e_models = [ReflexProbeDefault]

    def setUp(self) -> None:
        super().setUp()
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("DJANGO_ALLOW_ASYNC_UNSAFE", None)
        ReflexProbeDefault.objects.create(name="alpha")
        ReflexProbeDefault.objects.create(name="beta")

    # -- 1.360 ---------------------------------------------------------
    def test_1_360_run_orm_carries_synchronous_django_across_the_event_loop(self) -> None:
        """
        Scenario 1.360: synchronous ORM code runs from an async handler through ``run_orm``.
        Given: two records, and code running on an event loop -- as every
               Reflex handler does.
        When:  the ORM is called directly, then through ``run_orm`` and ``@orm``.
        Then:  directly, Django refuses (SynchronousOnlyOperation -- why the
               bridge exists); through the bridge the same query returns, a
               save with lifecycle hooks persists, and an exception raised by
               the code reaches the handler unchanged.
        """
        # The public path: `orm` is also what a submodule would be called, and a
        # submodule of that name would shadow the decorator once imported.
        from lex.lex_app.reflex import orm, run_orm

        async def direct():
            return ReflexProbeDefault.objects.count()

        with self.assertRaises(SynchronousOnlyOperation):
            async_to_sync(direct)()

        async def bridged():
            return await run_orm(lambda: sorted(ReflexProbeDefault.objects.values_list("name", flat=True)))

        self.assertEqual(async_to_sync(bridged)(), ["alpha", "beta"])

        @orm
        def create(name):
            return ReflexProbeDefault.objects.create(name=name).pk

        pk = async_to_sync(create)("gamma")
        self.assertTrue(ReflexProbeDefault.objects.filter(pk=pk, name="gamma").exists())

        @orm
        def fails():
            raise LookupError("from the ORM side")

        with self.assertRaisesRegex(LookupError, "from the ORM side"):
            async_to_sync(fails)()

    # -- 1.361 ---------------------------------------------------------
    def test_1_361_events_get_the_connection_handling_a_request_gets(self) -> None:
        """
        Scenario 1.361: stale connections are retired around every event, never mid-transaction.
        Given: a connection past its CONN_MAX_AGE, inside and outside a transaction.
        When:  ``close_stale_connections`` and the middleware's pre/post hooks run.
        Then:  an expired connection outside a transaction is closed -- the next
               query reconnects -- one inside ``atomic`` is left alone; the
               middleware retires it before the event without blocking the
               event, returns the update unchanged after it, and also retires
               the loop thread's connection when DJANGO_ALLOW_ASYNC_UNSAFE lets
               sync ORM run there.
        """
        from lex.lex_app.reflex import django_orm as orm_module
        from lex.lex_app.reflex.django_orm import DjangoConnectionMiddleware, close_stale_connections

        connection.ensure_connection()
        with transaction.atomic():
            connection.close_at = time.monotonic() - 1
            close_stale_connections()
            # Django's close() inside atomic keeps the object and only flags the
            # transaction as lost, so the flag -- and a working query -- is the test.
            self.assertFalse(connection.closed_in_transaction, "closing inside atomic discards the transaction")
            self.assertFalse(connection.needs_rollback)
            self.assertEqual(ReflexProbeDefault.objects.count(), 2, "the transaction must still be usable")

        connection.close_at = time.monotonic() - 1
        close_stale_connections()
        self.assertIsNone(connection.connection, "an expired connection must be retired")
        self.assertEqual(ReflexProbeDefault.objects.count(), 2, "and the next query reconnects")

        middleware = DjangoConnectionMiddleware()
        connection.ensure_connection()
        connection.close_at = time.monotonic() - 1
        self.assertIsNone(async_to_sync(middleware.preprocess)(app=None, state=None, event=None))
        self.assertIsNone(connection.connection, "preprocess must retire the ORM thread's stale connection")

        update = object()
        with patch.object(orm_module, "close_stale_connections", wraps=close_stale_connections) as spy:
            self.assertIs(
                async_to_sync(middleware.postprocess)(app=None, state=None, event=None, update=update),
                update,
            )
            self.assertEqual(spy.call_count, 1, "the ORM thread only, by default")
            os.environ["DJANGO_ALLOW_ASYNC_UNSAFE"] = "true"
            async_to_sync(middleware.postprocess)(app=None, state=None, event=None, update=update)
            self.assertEqual(spy.call_count, 3, "and the loop thread's own, when sync ORM may run there")


class TestCluster01aq_Dispatch(E2ETestCase):
    """``?model=&pk=`` resolves as ``streamlit_app.py`` resolves it."""

    e2e_models = [ReflexProbeFund, ReflexProbeDefault]
    e2e_framework_models = [ReflexProbePlain]

    def setUp(self) -> None:
        super().setUp()
        _PROJECT_APP.start()
        self.addCleanup(_PROJECT_APP.stop)
        self.fund = ReflexProbeFund.objects.create(name="Alpha")
        self.default = ReflexProbeDefault.objects.create(name="Plain LexModel")
        self.plain = ReflexProbePlain.objects.create(name="Not a LexModel")

    # -- 1.362 ---------------------------------------------------------
    def test_1_362_every_query_string_resolves_to_its_view(self) -> None:
        """
        Scenario 1.362: the query string decides the view, with Streamlit's checks in Streamlit's order.
        Given: a model with both hooks, a LexModel with neither, a plain model.
        When:  ``resolve_view`` resolves each query string.
        Then:  no model -> the project's own; an unknown one -> not found; a
               record -> its dashboard, the name made canonical; a missing or
               malformed pk -> not found; a model with no hooks at all -> cannot
               visualize; a model alone -> its table.
        """
        from lex.lex_app.reflex.dashboards import resolve_view

        def view(model, pk=None):
            return async_to_sync(resolve_view)(model, pk)

        pk, default_pk, plain_pk = str(self.fund.pk), str(self.default.pk), str(self.plain.pk)
        expected = {
            (None, None): ("structure", "", ""),
            ("  ", None): ("structure", "", ""),
            ("nope", None): ("missing_model", "nope", ""),
            ("ReflexProbeFund", pk): ("record", "reflexprobefund", pk),
            ("reflexprobefund", "999999"): ("missing_record", "reflexprobefund", "999999"),
            ("reflexprobefund", "not-a-pk"): ("missing_record", "reflexprobefund", "not-a-pk"),
            ("reflexprobedefault", default_pk): ("record", "reflexprobedefault", default_pk),
            ("reflexprobeplain", plain_pk): ("unsupported_record", "reflexprobeplain", plain_pk),
            ("reflexprobeplain", "999999"): ("missing_record", "reflexprobeplain", "999999"),
            ("reflexprobeplain", None): ("unsupported_table", "reflexprobeplain", ""),
            ("reflexprobefund", None): ("table", "reflexprobefund", ""),
        }
        for (model, record), result in expected.items():
            with self.subTest(model=model, pk=record):
                self.assertEqual(view(model, record), result)

    # -- 1.363 ---------------------------------------------------------
    def test_1_363_the_page_and_its_dashboards_read_the_url_through_the_orm(self) -> None:
        """
        Scenario 1.363: ``resolve`` and ``current_record`` work from the router, as an event sees it.
        Given: a page on ``?model=reflexprobefund&pk=<pk>``.
        When:  the page's ``resolve`` handler runs, and a dashboard's state
               asks for ``current_record``.
        Then:  the page shows the record view for that model and pk; the record
               comes back through the ORM; ``model=`` pins another class; no
               pk, or a missing record, is ``None``.
        """
        from lex.lex_app.reflex.dashboards import current_record

        root = _root_state(f"http://localhost:8502/?model=reflexprobefund&pk={self.fund.pk}")
        page = _substate(root, LexDashboardState)
        async_to_sync(LexDashboardState.resolve.fn)(page)
        self.assertEqual((page.view, page.model, page.pk), ("record", "reflexprobefund", str(self.fund.pk)))

        record = async_to_sync(current_record)(page)
        self.assertIsInstance(record, ReflexProbeFund)
        self.assertEqual(record.name, "Alpha")

        pinned = _substate(_root_state(f"http://x/?model=anything&pk={self.default.pk}"), LexDashboardState)
        self.assertEqual(async_to_sync(current_record)(pinned, ReflexProbeDefault).name, "Plain LexModel")

        for url in ("http://x/?model=reflexprobefund", "http://x/?model=reflexprobefund&pk=999999",
                    "http://x/?model=nope&pk=1", "http://x/"):
            with self.subTest(url=url):
                self.assertIsNone(async_to_sync(current_record)(_substate(_root_state(url), LexDashboardState)))


class TestCluster01aq_PageChrome(TestCase):
    """Framed by lex-app, the page draws none of its own chrome."""

    # -- 1.364 ---------------------------------------------------------
    def test_1_364_embed_and_logout_flags_follow_lex_apps_contract(self) -> None:
        """
        Scenario 1.364: ``lex_embed`` hides the top bar; ``is_logout_enabled`` the sign-out.
        Given: the query parameters lex-app adds when it frames a dashboard.
        When:  the page reads them.
        Then:  only a truthy ``lex_embed`` counts as framed; sign-out is shown
               unless ``is_logout_enabled`` is explicitly falsy.
        """
        embedded = {"": False, "?lex_embed=1": True, "?lex_embed=true": True,
                    "?lex_embed=0": False, "?lex_embed=": False}
        for query, expected in embedded.items():
            with self.subTest(query=query):
                self.assertIs(_substate(_root_state(f"http://x/{query}"), LexDashboardState).embedded, expected)
        logout = {"": True, "?is_logout_enabled=false": False, "?is_logout_enabled=0": False,
                  "?is_logout_enabled=true": True, "?is_logout_enabled=": True}
        for query, expected in logout.items():
            with self.subTest(query=query):
                self.assertIs(_substate(_root_state(f"http://x/{query}"), LexDashboardState).logout_enabled, expected)


class _BadInstanceHook:
    _meta = SimpleNamespace(model_name="badinstancehook")

    def reflex_main(self):  # the Streamlit shape, which cannot work here
        return rx.text("never")


class _BadReturnHook:
    _meta = SimpleNamespace(model_name="badreturnhook")

    @classmethod
    def reflex_class_main(cls):
        return "not a component"


class TestCluster01aq_DispatchPage(TestCase):
    """The page compiled for ``/`` holds each dashboard a model supplies."""

    # -- 1.365 ---------------------------------------------------------
    def test_1_365_the_page_compiles_supplied_hooks_and_refuses_broken_ones(self) -> None:
        """
        Scenario 1.365: only overriding models are compiled in; a broken hook fails at compile.
        Given: a model with both hooks, a LexModel with neither, a plain model.
        When:  the page component is built.
        Then:  the supplied hooks are in it and the others fall to the shared
               notice; a hook written as an instance method (Streamlit's shape)
               or returning a non-component is refused with a message that
               says what to write instead.
        """
        from lex.lex_app.reflex.dashboards import NO_RECORD_DASHBOARD, dashboard_page, defines_hook

        self.assertTrue(defines_hook(ReflexProbeFund, "reflex_main"))
        self.assertTrue(defines_hook(ReflexProbeFund, "reflex_class_main"))
        self.assertFalse(defines_hook(ReflexProbeDefault, "reflex_main"), "LexModel's default is not a dashboard")
        self.assertFalse(defines_hook(ReflexProbePlain, "reflex_main"))

        models = {"reflexprobefund": ReflexProbeFund, "reflexprobedefault": ReflexProbeDefault,
                  "reflexprobeplain": ReflexProbePlain}
        page = str(dashboard_page(None, models=lambda: models)())
        self.assertIn("probe record dashboard", page)
        self.assertIn("probe table dashboard", page)
        self.assertIn(NO_RECORD_DASHBOARD, page)
        self.assertIn("_reflex_structure.py", page, "no structure module: the placeholder says what to add")

        with self.assertRaisesRegex(TypeError, "@classmethod"):
            dashboard_page(None, models=lambda: {"badinstancehook": _BadInstanceHook})()
        with self.assertRaisesRegex(TypeError, "must return a Reflex component"):
            dashboard_page(None, models=lambda: {"badreturnhook": _BadReturnHook})()

        default = ReflexProbeDefault.reflex_main()
        self.assertIsInstance(default, rx.Component)
        self.assertIn(NO_RECORD_DASHBOARD, str(default))
        self.assertIn("No class-level visualization", str(ReflexProbeDefault.reflex_class_main()))

    def test_1_365b_the_structure_module_is_optional_but_never_silently_broken(self) -> None:
        """
        Scenario 1.365: ``_reflex_structure.py`` -- absent is fine, broken is loud.
        Given: no structure module; one with no main(); one whose main()
               returns a non-component; one that fails to import.
        When:  the structure is loaded and rendered.
        Then:  absent -> the placeholder; a bad main() -> a TypeError naming
               the module; an import failure propagates instead of turning the
               dashboard into the placeholder.
        """
        from lex.lex_app.reflex.dashboards import load_structure, structure_main

        self.assertIsNone(load_structure("lex_1aq_nothing_here"))
        self.assertIn("_reflex_structure.py", str(structure_main(None)))
        self.assertIn("_reflex_structure.py", str(structure_main(SimpleNamespace(__name__="s"))))
        with self.assertRaisesRegex(TypeError, "must return a Reflex component"):
            structure_main(SimpleNamespace(__name__="s", main=lambda: "text"))

        with tempfile.TemporaryDirectory() as tmp:
            package = Path(tmp) / "lex_1aq_broken"
            package.mkdir()
            (package / "__init__.py").write_text("")
            (package / "_reflex_structure.py").write_text("raise ImportError('broken on purpose')\n")
            sys.path.insert(0, tmp)
            try:
                with self.assertRaisesRegex(ImportError, "broken on purpose"):
                    load_structure("lex_1aq_broken")
            finally:
                sys.path.remove(tmp)
                for name in [m for m in sys.modules if m.startswith("lex_1aq_broken")]:
                    del sys.modules[name]


class TestCluster01aq_TheCompiledApp(TestCase):
    """``lex.reflex_app``, compiled as ``reflex run`` compiles it -- once per process.

    The auth plugin binds itself to the process on the first compile and
    refuses a second one, so every assertion about the compiled app reads
    this one compile.
    """

    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        # One compile per process, shared with 1bt: see _reflex_compiled.py.
        from lex.test_project.tests.init._reflex_compiled import compiled_app

        compiled = compiled_app()
        cls.pages = compiled.pages
        cls.index_on_load = compiled.index_on_load
        cls.middlewares = compiled.middlewares
        cls.plugins = compiled.plugins

    # -- 1.366 ---------------------------------------------------------
    def test_1_366_the_app_signs_in_through_keycloak_and_serves_every_dashboard(self) -> None:
        """
        Scenario 1.366: the compiled app is the whole contract, end to end.
        Given: a project whose rxconfig.py is the one ``lex reflex`` writes,
               the reference ``_reflex_structure.py``, and a model with hooks.
        When:  ``lex.reflex_app`` is imported and compiled.
        Then:  ``/``, ``/logout`` and the structure's own pages (the reference
               example's ``/records``, ``/grid``, ``/in-lex-app``,
               ``/calculations``, ``/edit`` since 1bw) are compiled in, with no
               Reflex Enterprise sign-in pages; ``/`` loads the user's grants
               before resolving the URL; lex-app's session middleware runs
               first, then RFDS's policy gate, then the Django connection
               middleware; the plugins are Radix and RFDS only; ``main()`` and
               the model's dashboards are on ``/``.
        """
        for route in ("index", "logout", "records", "grid", "in-lex-app", "calculations", "edit"):
            self.assertIn(route, self.pages, f"missing page {route}: {sorted(self.pages)}")
        for gone in ("login", "callback", "_reflex_oidc_lex_keycloak/popup-login"):
            self.assertNotIn(gone, self.pages, "Reflex Enterprise's sign-in pages are gone")

        handlers = [handler.fn.__qualname__ for handler in self.index_on_load]
        self.assertEqual(handlers, ["LexAccessState.load", "LexDashboardState.resolve"],
                         "the grants first; an anonymous session's events go through the sign-in")
        names = [type(m).__name__ for m in self.middlewares]
        self.assertEqual(names[:3], ["LexSessionMiddleware", "PolicyMiddleware", "DjangoConnectionMiddleware"],
                         "the session middleware must see every event first")
        self.assertEqual([type(p).__name__ for p in self.plugins], ["RadixThemesPlugin", "RFDSPlugin"])

        index = str(self.pages["index"])
        self.assertIn("probe record dashboard", index)
        self.assertIn("probe table dashboard", index)
        self.assertIn("Overview", index, "the structure's main() is the default view")


class TestCluster01aq_Registration(TestCase):
    """The Reflex report joins lex-app's sidebar only when asked to."""

    # -- 1.367 ---------------------------------------------------------
    def test_1_367_the_reflex_report_is_gated_and_discovery_never_imports_it_twice(self) -> None:
        """
        Scenario 1.367: the ``Reflex`` report, its sidebar entry, and what discovery skips.
        Given: IS_REFLEX_ENABLED unset, then ``true``.
        When:  the report renders, the structure is built, models register,
               and discovery filters files and directories.
        Then:  the report frames REFLEX_URL (default :8502) in lex-app's embed
               mode (``lex_embed=1``: no second user bar); the sidebar entry
               and the registration appear only when enabled; discovery skips
               ``_reflex_structure.py`` and ``rxconfig.py``, and the ``reflex``
               directory only inside lex's own packages -- so no
               ``lex_app.reflex.*`` duplicate of a module exists in this process.
        """
        from lex.lex_app.apps import LexAppConfig
        from lex.lex_app.reflex.Reflex import Reflex, reflex_enabled
        from lex.lex_app.streamlit.Streamlit import Streamlit
        from lex.process_admin.utils.model_structure_builder import ModelStructureBuilder
        from lex.utilities.config.generic_app_config import GenericAppConfig, _is_structure_file

        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("REFLEX_URL", None)
            os.environ.pop("IS_REFLEX_ENABLED", None)
            self.assertIn('src="http://localhost:8502/?lex_embed=1"', Reflex().get_html(None))
            self.assertFalse(reflex_enabled())
            builder = ModelStructureBuilder(repo="demo")
            builder._add_reports_to_structure()
            self.assertNotIn("Reflex", builder.model_structure)
            registered_off = self._register_reports(LexAppConfig)
            os.environ["REFLEX_URL"] = "https://dash.example.com/"
            self.assertIn('src="https://dash.example.com/?lex_embed=1"', Reflex().get_html(None))

            os.environ["IS_REFLEX_ENABLED"] = "true"
            builder = ModelStructureBuilder(repo="demo")
            builder._add_reports_to_structure()
            self.assertEqual(builder.model_structure["Reflex"], {"reflex": None})
            registered_on = self._register_reports(LexAppConfig)
            generic_on = self._register_reports(GenericAppConfig)
        self.assertEqual(registered_off, [Streamlit])
        self.assertEqual(registered_on, [Streamlit, Reflex])
        self.assertEqual(generic_on, [Streamlit, Reflex])

        self.assertFalse(_is_structure_file("_reflex_structure.py"), "the Reflex app's to import")
        self.assertTrue(_is_structure_file("_streamlit_structure.py"))
        config = GenericAppConfig.__new__(GenericAppConfig)
        self.assertFalse(config._is_valid_module("rxconfig", "rxconfig.py"))
        self.assertTrue(config._dir_filter("reflex"), "a project's own `reflex` folder is still discovered")
        config._discovering_lex_package = True
        self.assertFalse(config._dir_filter("reflex"))
        self.assertTrue(config._dir_filter("streamlit"))
        duplicates = sorted(m for m in sys.modules if m.startswith("lex_app.reflex"))
        self.assertEqual(duplicates, [], "a second copy of the Reflex package defines every state twice")

    @staticmethod
    def _register_reports(config_class) -> list:
        """The HTMLReport list a config's ``register_models`` hands to registration."""
        config = config_class.__new__(config_class)
        config.discovered_models = {}
        config.untracked_models = []
        config.model_structure_builder = SimpleNamespace(
            history_tracking_enabled=True, model_structure={}, model_structure_is_explicitly_defined=False,
            model_styling={}, widget_structure=[],
        )
        with patch("lex.process_admin.utils.model_registration.ModelRegistration.register_models") as register, \
                patch("lex.process_admin.utils.model_registration.ModelRegistration.register_model_styling"), \
                patch("lex.process_admin.utils.model_registration.ModelRegistration.register_widget_structure"):
            config.register_models()
        from lex.lex_app.streamlit.Streamlit import Streamlit

        return next(call.args[0] for call in register.call_args_list if Streamlit in call.args[0])


class TestCluster01aq_EventLoops(TestCase):
    """Django setup survives the event loop the Reflex server runs."""

    # -- 1.368 ---------------------------------------------------------
    def test_1_368_django_setup_on_granians_uvloop_patches_nothing(self) -> None:
        """
        Scenario 1.368: ``AppConfig.ready()`` does not fail on granian's uvloop loop.
        Given: a downstream project in a fresh process whose current loop is
               uvloop's, which granian installs before Django is set up.
        When:  Django is set up (``LexAppConfig.ready()`` runs the project's
               startup), and the main thread then calls ``async_to_sync``
               twice, each time with thread-sensitive work.
        Then:  setup carries on, ``asyncio.run`` is still the standard
               library's, and both calls return.

        Rewritten for BUG-046. ``ready()`` used to call ``nest_asyncio.apply()``
        and skip it only where uvloop refused the patch. Now it patches nothing
        on any loop (1bo, scenarios 1.665-1.666).

        It runs in a subprocess because what is under test is process-wide
        state. ``PYTHONPATH`` points the child at this checkout. Without it, a
        checkout that is not pip-installed cannot import lex there.
        """
        project = Path(tempfile.mkdtemp(prefix="cluster1aq_")).resolve()
        self.addCleanup(shutil.rmtree, project, ignore_errors=True)
        (project / "__init__.py").write_text("")
        (project / "lex_config.py").write_text(f'PROJECT_GROUPS = ["{project.name}"]\n')
        code = (
            "import asyncio, faulthandler, json, threading, uvloop\n"
            "import lex.bin.lex\n"
            "loop = uvloop.new_event_loop(); asyncio.set_event_loop(loop)\n"
            "import django\n"
            "django.setup()\n"
            "from asgiref.sync import async_to_sync, sync_to_async\n"
            "from lex.lex_app.settings import repo_name\n"
            "async def calling_thread():\n"
            "    return await sync_to_async(threading.get_ident, thread_sensitive=True)()\n"
            "faulthandler.dump_traceback_later(60, exit=True)\n"
            "calls = [async_to_sync(calling_thread)() == threading.get_ident() for _ in range(2)]\n"
            "faulthandler.cancel_dump_traceback_later()\n"
            "print(json.dumps({'project': repo_name, 'asyncio.run': asyncio.run.__module__, 'calls': calls}))\n"
        )
        cleared = ("DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "CALLED_FROM_START_COMMAND")
        env = {key: value for key, value in os.environ.items() if key not in cleared}
        env["PYTHONPATH"] = Path(__file__).resolve().parents[4].as_posix()
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
            cwd=project, env=env,
        )
        self.assertEqual(
            result.returncode, 0,
            msg=f"setup must carry on on uvloop, and no call may hang:\n{result.stderr[-3000:]}",
        )
        report = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(report["project"], project.name, "precondition: the downstream project is the one set up")
        self.assertEqual(
            report["asyncio.run"], "asyncio.runners",
            "setup on uvloop must leave asyncio.run as the standard library's",
        )
        self.assertEqual(report["calls"], [True, True], "both async_to_sync calls must return after setup")
