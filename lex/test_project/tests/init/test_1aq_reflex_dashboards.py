"""Cluster 1aq: Reflex dashboards at run time -- Keycloak sign-in, the Django
ORM from event handlers, and the ``?model=&pk=`` dispatch.

Intent
------
A Reflex dashboard must give its author what a Streamlit dashboard gives:

* **Sign-in against lex-app's Keycloak**, with nothing new to configure. The
  Streamlit proxy is replaced by Reflex Enterprise's ``AuthPlugin``, and
  ``LexKeycloakAuthState`` points it at the realm and confidential client the
  project already has (``KEYCLOAK_URL``, ``KEYCLOAK_REALM``,
  ``OIDC_RP_CLIENT_ID/SECRET``), honours ``OIDC_ISSUER`` and
  ``OIDC_VERIFY_SSL`` as the proxy does, and never asks for ``offline_access``,
  so a dashboard session ends with the Keycloak session as every lex-app session
  does. ``KEYCLOAK_CLIENT_ID`` -- the
  *browser's* client in lex-app -- must never be picked up by accident.
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

Cluster 1aq -- scenarios 1.357-1.369. Type: U / I.
Covers: lex/lex_app/reflex/ (config.py, auth.py ``LexKeycloakAuthState`` and
``LexUser``, django_orm.py, dashboards.py, app.py, Reflex.py,
examples/_reflex_structure.py), lex/reflex_app.py,
lex/utilities/config/generic_app_config.py, lex/lex_app/apps.py
(``_apply_nest_asyncio``, ``register_models``),
lex/process_admin/utils/model_structure_builder.py, lex/core/models/LexModel.py
(``reflex_main`` / ``reflex_class_main``).
Run: python -m lex pytest lex/test_project/tests/init/test_1aq_reflex_dashboards.py -v
"""

from __future__ import annotations

import contextlib
import importlib
import os
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
from lex.lex_app.reflex.auth import LexKeycloakAuthState, LexUser
from lex.lex_app.reflex.dashboards import LexDashboardState
from lex.test_project.tests._e2e_test_case import E2ETestCase

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

_OIDC_ENV = (
    "LEX_KEYCLOAK_ISSUER_URI", "LEX_KEYCLOAK_CLIENT_ID", "LEX_KEYCLOAK_CLIENT_SECRET",
    "OIDC_ISSUER_URI", "OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
    "KEYCLOAK_URL", "KEYCLOAK_REALM", "KEYCLOAK_REALM_NAME", "KEYCLOAK_CLIENT_ID",
    "OIDC_RP_CLIENT_ID", "OIDC_RP_CLIENT_SECRET", "OIDC_ISSUER", "OIDC_VERIFY_SSL",
)


def _root_state(url: str = "http://localhost:8502/"):
    """A root Reflex state whose router is on ``url``, the way an event sees it."""
    from reflex.istate.data import ReflexURL, RouterData
    from reflex.state import State

    root = State()
    root.router = RouterData(url=ReflexURL(url))
    return root


def _substate(root, state_cls):
    return root.get_substate(state_cls.get_full_name().split("."))


@contextlib.contextmanager
def _oidc_env(**values: str):
    """Exactly ``values`` for every variable the provider may read."""
    with patch.dict(os.environ, {}, clear=False):
        for name in _OIDC_ENV:
            os.environ.pop(name, None)
        os.environ.update(values)
        yield


class TestCluster01aq_Config(TestCase):
    """What rxconfig.py returns: lex-app's app module, sign-in, no ports."""

    # -- 1.357 ---------------------------------------------------------
    def test_1_357_lex_config_wires_the_app_module_sign_in_and_defaults(self) -> None:
        """
        Scenario 1.357: ``lex_config()`` is an rxe.Config wired for Lex App.
        Given: no overrides.
        When:  ``lex_config()`` builds the config.
        Then:  Reflex imports ``lex.reflex_app``; telemetry is off; the
               AuthPlugin signs in through the lex Keycloak provider, named by
               import path, never asking for ``offline_access`` (an offline
               token outlives the Keycloak session); Radix is explicit
               and the sitemap off; no port is configured (a configured port
               breaks ``--backend-only`` and ``--env prod``); an override wins.
        """
        import reflex_enterprise as rxe
        from reflex_enterprise.plugins.auth import AuthPlugin

        from lex.lex_app.reflex.config import lex_auth_plugin, lex_config

        config = lex_config()
        self.assertIsInstance(config, rxe.Config)
        self.assertEqual(config.app_module_import, "lex.reflex_app")
        self.assertFalse(config.telemetry_enabled)
        self.assertIsNone(config.frontend_port, "a configured port breaks --backend-only")
        self.assertIsNone(config.backend_port, "two configured ports break --env prod")

        auth = [p for p in config.plugins if isinstance(p, AuthPlugin)]
        self.assertEqual(len(auth), 1, f"exactly one AuthPlugin, got {config.plugins}")
        self.assertEqual(auth[0]._auth_providers, ["lex.lex_app.reflex.auth.LexKeycloakAuthState"])
        self.assertNotIn(
            "offline_access", auth[0].extra_scopes,
            "Keycloak refreshes without it; with it, signing out of lex-app no longer ends the session",
        )
        self.assertTrue(any(isinstance(p, rx.plugins.RadixThemesPlugin) for p in config.plugins))
        self.assertIn(rx.plugins.SitemapPlugin, config.disable_plugins)

        self.assertTrue(lex_config(telemetry_enabled=True).telemetry_enabled, "an override must win")

        plugin = lex_auth_plugin(extra_scopes=["groups"], auth=False)
        self.assertEqual(plugin.extra_scopes, ["groups"], "a project's scopes are requested as given")
        self.assertIs(plugin.auth, False, "AuthPlugin options pass through")

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


class TestCluster01aq_KeycloakProvider(TestCase):
    """The provider signs in against lex-app's own Keycloak settings."""

    def _provider(self):
        return _substate(_root_state(), LexKeycloakAuthState)

    # -- 1.358 ---------------------------------------------------------
    def test_1_358_settings_resolve_lex_keycloak_then_oidc_then_lex_app(self) -> None:
        """
        Scenario 1.358: the provider's config comes from where a project already has it.
        Given: only lex-app's own Keycloak settings, then Reflex's shared
               ``OIDC_*`` and provider-specific ``LEX_KEYCLOAK_*`` on top.
        When:  the plugin asks the provider for its issuer and client.
        Then:  lex-app's settings are the fallback -- the realm URL built from
               KEYCLOAK_URL/KEYCLOAK_REALM, the confidential RP client -- the
               more specific variable always wins, and KEYCLOAK_CLIENT_ID (the
               browser's client) is never used.
        """
        provider = self._provider()
        with _oidc_env(
            KEYCLOAK_URL="https://kc.example.com/", KEYCLOAK_REALM="lex",
            OIDC_RP_CLIENT_ID="lex-rp", OIDC_RP_CLIENT_SECRET="rp-secret",
            KEYCLOAK_CLIENT_ID="lex-browser",
        ):
            self.assertEqual(async_to_sync(provider._issuer_uri)(), "https://kc.example.com/realms/lex")
            self.assertEqual(async_to_sync(provider._client_id)(), "lex-rp",
                             "KEYCLOAK_CLIENT_ID is the browser's client, not this one")
            self.assertEqual(async_to_sync(provider._client_secret)(), "rp-secret")

            os.environ["OIDC_CLIENT_ID"] = "shared"
            self.assertEqual(async_to_sync(provider._client_id)(), "shared")
            os.environ["LEX_KEYCLOAK_CLIENT_ID"] = "specific"
            self.assertEqual(async_to_sync(provider._client_id)(), "specific")

        with _oidc_env(KEYCLOAK_URL="https://kc.example.com", KEYCLOAK_REALM_NAME="legacy"):
            self.assertEqual(
                async_to_sync(provider._issuer_uri)(), "https://kc.example.com/realms/legacy",
                "KEYCLOAK_REALM_NAME is the realm fallback KeycloakManager also honours",
            )

        with _oidc_env():
            with self.assertRaisesRegex(RuntimeError, "client ID not configured"):
                async_to_sync(provider._client_id)()
            with self.assertRaisesRegex(RuntimeError, "issuer URI not configured"):
                async_to_sync(provider._issuer_uri)()

    # -- 1.359 ---------------------------------------------------------
    def test_1_359_issuer_tls_and_label_follow_the_proxys_settings(self) -> None:
        """
        Scenario 1.359: OIDC_ISSUER, OIDC_VERIFY_SSL and the label behave as for the proxy.
        Given: the provider, with and without OIDC_ISSUER / OIDC_VERIFY_SSL.
        When:  the plugin asks which issuers to accept, and for its HTTP client.
        Then:  without OIDC_ISSUER the plugin's own check stands (None); with it,
               both the configured and the expected issuer are accepted; TLS is
               verified unless OIDC_VERIFY_SSL turns it off; the variables are
               ``LEX_KEYCLOAK_*`` and the button reads "Login with Keycloak".
        """
        from reflex_enterprise.auth.oidc.utils import DEFAULT_HTTP_CLIENT

        import lex.lex_app.reflex.auth as auth

        provider = self._provider()
        self.assertEqual(LexKeycloakAuthState.display_name(), "Keycloak")
        self.assertEqual(
            LexKeycloakAuthState._env_keys("client_id"), ("LEX_KEYCLOAK_CLIENT_ID", "OIDC_CLIENT_ID")
        )

        with _oidc_env(KEYCLOAK_URL="http://keycloak:8080", KEYCLOAK_REALM="lex"):
            self.assertIsNone(async_to_sync(provider._valid_issuers)())
            os.environ["OIDC_ISSUER"] = "https://auth.example.com/realms/lex/"
            accepted = async_to_sync(provider._valid_issuers)()
        self.assertIn("https://auth.example.com/realms/lex", accepted)
        self.assertIn("https://auth.example.com/realms/lex/", accepted)
        self.assertIn("http://keycloak:8080/realms/lex", accepted)

        with _oidc_env():
            self.assertIs(LexKeycloakAuthState._http_client(), DEFAULT_HTTP_CLIENT)
        with _oidc_env(OIDC_VERIFY_SSL="false"), patch.object(auth, "_UNVERIFIED_HTTP_CLIENT", None), \
                patch.object(auth.httpx, "AsyncClient") as async_client:
            client = LexKeycloakAuthState._http_client()
        async_client.assert_called_once_with(verify=False)
        self.assertIsNot(client, DEFAULT_HTTP_CLIENT)

    # -- 1.369 ---------------------------------------------------------
    def test_1_369_lex_user_projects_the_names_lex_app_shows(self) -> None:
        """
        Scenario 1.369: ``LexUser`` adds Keycloak's username and the best name to greet by.
        Given: signed-in claims with and without a display name.
        When:  ``LexUser.username`` / ``display_name`` are read.
        Then:  the username is ``preferred_username``; the display name is the
               name, else the username, else the email -- empty when signed out.
        """
        from reflex_enterprise.auth import AuthUserState

        cases = [
            ({"sub": "1", "name": "Ada Lovelace", "preferred_username": "ada", "email": "a@x"},
             "ada", "Ada Lovelace"),
            ({"sub": "1", "preferred_username": "ada", "email": "a@x"}, "ada", "ada"),
            ({"sub": "1", "email": "a@x"}, "", "a@x"),
            ({}, "", ""),
        ]
        for claims, username, display_name in cases:
            with self.subTest(claims=claims):
                root = _root_state()
                if claims:
                    _substate(root, AuthUserState)._set_user(claims, "lex_keycloak")
                user = _substate(root, LexUser)
                self.assertEqual(user.username, username)
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
        from reflex.config import get_config
        from reflex_base.registry import RegistrationContext

        from lex.bin.lex import _ensure_reflex_config
        from lex.lex_app.reflex.examples import _reflex_structure as example

        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), \
                patch.dict(os.environ, {"CI": "true"}), _PROJECT_APP, \
                patch("lex.lex_app.reflex.dashboards.load_structure", return_value=example), \
                RegistrationContext.ensure_context().fork():
            # The project's rxconfig.py, exactly as `lex reflex` writes it.
            _ensure_reflex_config(Path(tmp))
            sys.modules.pop("lex.reflex_app", None)
            try:
                module = importlib.import_module("lex.reflex_app")
                app = module.app
                app._compile(dry_run=True)
                cls.pages = dict(app._pages)
                cls.index_on_load = list(app._unevaluated_pages["index"].on_load)
                cls.middlewares = list(app._middlewares)
                cls.plugins = list(get_config().plugins)
            finally:
                sys.modules.pop("lex.reflex_app", None)

    # -- 1.366 ---------------------------------------------------------
    def test_1_366_the_app_signs_in_through_keycloak_and_serves_every_dashboard(self) -> None:
        """
        Scenario 1.366: the compiled app is the whole contract, end to end.
        Given: a project whose rxconfig.py is the one ``lex reflex`` writes,
               the reference ``_reflex_structure.py``, and a model with hooks.
        When:  ``lex.reflex_app`` is imported and compiled.
        Then:  sign-in routes and the Keycloak provider's popup routes exist;
               ``/`` runs the sign-in guard before resolving the URL; the
               Django connection middleware is installed; the provider asks
               Keycloak for the standard scopes and not ``offline_access``; the
               structure's own page
               (``/about``) and ``main()``, and the model's dashboards, are
               compiled in.
        """
        from reflex_enterprise.plugins.auth import AuthPlugin

        for route in ("index", "login", "callback", "logout", "forbidden", "about",
                      "_reflex_oidc_lex_keycloak/popup-login", "_reflex_oidc_lex_keycloak/popup-logout"):
            self.assertIn(route, self.pages, f"missing page {route}: {sorted(self.pages)}")

        handlers = [handler.fn.__qualname__ for handler in self.index_on_load]
        self.assertEqual(
            handlers, ["PageGuardState.enforce_login", "LexDashboardState.resolve"],
            "the guard must run first: resolve reads the database only for a signed-in user",
        )
        self.assertIn("DjangoConnectionMiddleware", [type(m).__name__ for m in self.middlewares])

        auth = next(p for p in self.plugins if isinstance(p, AuthPlugin))
        self.assertEqual(auth.auth_providers, [LexKeycloakAuthState])
        requested = LexKeycloakAuthState.backend_vars["_requested_scopes"].split()
        self.assertTrue({"openid", "email", "profile"} <= set(requested), requested)
        self.assertNotIn("offline_access", requested)

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
    def test_1_368_nest_asyncio_is_skipped_on_a_loop_it_cannot_patch(self) -> None:
        """
        Scenario 1.368: ``AppConfig.ready()`` does not fail on granian's uvloop loop.
        Given: a process whose current loop is uvloop's -- what granian installs
               before Django is set up -- then one on a plain asyncio loop.
        When:  ``_apply_nest_asyncio`` runs on each.
        Then:  on uvloop, where ``nest_asyncio.apply()`` raises, setup carries
               on; on asyncio's own loop the patch is still applied.

        In a subprocess: ``nest_asyncio.apply()`` patches asyncio for the
        whole process, which must not leak into the rest of the suite.
        """
        code = (
            "import asyncio, nest_asyncio, uvloop\n"
            "from lex.lex_app.apps import _apply_nest_asyncio\n"
            "loop = uvloop.new_event_loop(); asyncio.set_event_loop(loop)\n"
            "try:\n"
            "    nest_asyncio.apply(loop)\n"
            "except ValueError:\n"
            "    print('precondition: refused')\n"
            "_apply_nest_asyncio()\n"
            "print('uvloop:', getattr(loop, '_nest_patched', False))\n"
            "asyncio.set_event_loop(None); loop.close()\n"
            "loop = asyncio.new_event_loop(); asyncio.set_event_loop(loop)\n"
            "_apply_nest_asyncio()\n"
            "print('asyncio:', getattr(loop, '_nest_patched', False))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code], capture_output=True, text=True, timeout=300,
            cwd=tempfile.gettempdir(),
        )
        self.assertEqual(result.returncode, 0, msg=f"setup must carry on on uvloop:\n{result.stderr[-2000:]}")
        lines = result.stdout.splitlines()
        self.assertIn("precondition: refused", lines, "nest_asyncio itself must refuse uvloop")
        self.assertIn("uvloop: False", lines)
        self.assertIn("asyncio: True", lines, "a patchable loop must still be patched")
