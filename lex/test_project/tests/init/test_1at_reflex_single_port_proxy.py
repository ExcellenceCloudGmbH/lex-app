"""Cluster 1at: one port serves a Reflex development run -- the backend and the pages.

Intent
------
A pod exposes one port, and a Reflex development run has two servers: Vite for
the pages, and the backend for ``/_event``, ``/_upload``, ``/ping`` and the
sign-in routes. Reflex Enterprise's single-port mode (``use_single_port``,
``REFLEX_USE_SINGLE_PORT``) joins them -- the backend answers its own routes and
passes every other request on to the frontend server -- and that is how the
platform starts a project's dashboards. On reflex-enterprise 0.9.6 with Reflex
0.9.12 the mode did nothing but log "Unable to find the base Starlette app":
Reflex passes a lifespan task the Reflex app under a parameter named ``app`` and
the Starlette app only as ``starlette_app``, and the proxy names its parameter
``app``. lex-app hands it the Starlette app, so:

* a project's ``rxconfig.py`` repairs the proxy as Reflex loads it --
  ``lex_config()`` in the one ``lex reflex`` writes, ``lex_auth_plugin()`` in a
  hand-written one -- and the configuration is still the project's;
* run the way Reflex runs it, the backend's port answers the backend's routes
  itself and every other path, query string included, from the frontend server;
* repairing again changes nothing, and a proxy that already takes the
  Starlette app -- a fixed Reflex Enterprise -- or names no ``app`` parameter is
  left exactly as it is.

A regression reads as "the dashboard's URL answers /ping and 404s every page".

Cluster 1at -- scenarios 1.381-1.383. Type: U.
Covers: lex/lex_app/reflex/config.py (``lex_auth_plugin``, ``_repair_single_port_proxy``).
Run: python -m lex pytest lex/test_project/tests/init/test_1at_reflex_single_port_proxy.py -v
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest
import reflex_enterprise as rxe
from reflex.app_mixins.lifespan import LifespanMixin
from reflex_enterprise import proxy
from reflex_enterprise.app import AppEnterprise
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from lex.lex_app.reflex.config import lex_auth_plugin

pytestmark = pytest.mark.init

#: Variables that would change the configuration under test, cleared so the host's own cannot leak in.
_REFLEX_ENV = (
    "REFLEX_USE_SINGLE_PORT", "REFLEX_FRONTEND_PORT", "REFLEX_BACKEND_PORT", "REFLEX_BACKEND_ONLY",
)

#: A hand-written rxconfig.py that keeps lex-app's sign-in, as a project with its own app module has.
_HAND_WRITTEN_RXCONFIG = '''
import reflex_enterprise as rxe

from lex.lex_app.reflex import lex_auth_plugin

config = rxe.Config(app_name="hand_written", plugins=[lex_auth_plugin()])
'''

#: What a fresh Reflex process reports once it has loaded the project's configuration.
_LOAD_CONFIG = (
    "import inspect\n"
    "from reflex.config import get_config\n"
    "config = get_config()\n"
    "from reflex_enterprise import proxy\n"
    "print(config.app_name, config.module,\n"
    "      ','.join(inspect.signature(proxy.proxy_middleware).parameters))\n"
)


def _parameters(function) -> list[str]:
    """The parameter names Reflex sees when it decides what to pass a lifespan task."""
    return list(inspect.signature(function).parameters)


@contextlib.contextmanager
def _shipped_proxy():
    """Reflex Enterprise's proxy module as it ships, and whatever it held before restored afterwards."""
    before = proxy.proxy_middleware
    importlib.reload(proxy)
    try:
        yield
    finally:
        proxy.proxy_middleware = before


@contextlib.contextmanager
def _frontend_server():
    """A stand-in for Vite on a free port, answering every GET with the path it was asked for."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - the name http.server dispatches to
            body = f"frontend:{self.path}".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:
            """Silent: the suite's output is the assertions'."""

    server = ThreadingHTTPServer(("localhost", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@contextlib.contextmanager
def _clean_reflex_env():
    with patch.dict(os.environ, {}, clear=False):
        for name in _REFLEX_ENV:
            os.environ.pop(name, None)
        yield


class TestCluster01at_SinglePortProxy(TestCase):
    """Reflex Enterprise's single-port proxy, given the Starlette app by lex-app."""

    # -- 1.381 ---------------------------------------------------------
    def test_1_381_a_projects_rxconfig_repairs_the_proxy_as_reflex_loads_it(self) -> None:
        """
        Scenario 1.381: a project's rxconfig.py repairs the proxy as Reflex loads it.
        Given: a project whose rxconfig.py is the one ``lex reflex`` writes, or a
               hand-written one that keeps lex-app's sign-in, and a fresh
               interpreter -- the way every Reflex worker starts.
        When:  Reflex loads the configuration, before it would build the app.
        Then:  the configuration is the project's own, and Reflex Enterprise's
               proxy takes ``starlette_app``, the Starlette app Reflex passes.
        """
        from lex.bin.lex import _ensure_reflex_config

        cases = {
            "written by lex reflex": ("lex.reflex_app", None),
            "hand-written": ("hand_written.hand_written", _HAND_WRITTEN_RXCONFIG),
        }
        for case, (app_module, source) in cases.items():
            with self.subTest(rxconfig=case), tempfile.TemporaryDirectory() as tmp, _clean_reflex_env():
                if source is None:
                    _ensure_reflex_config(Path(tmp))
                else:
                    (Path(tmp) / "rxconfig.py").write_text(source, encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, "-c", _LOAD_CONFIG], capture_output=True, text=True,
                    timeout=300, cwd=tmp,
                )
                self.assertEqual(result.returncode, 0, msg=result.stderr[-2000:])
                app_name, loaded_module, parameters = result.stdout.strip().splitlines()[-1].split()
                self.assertEqual(
                    loaded_module, app_module, f"Reflex must still load the project's configuration ({case})",
                )
                self.assertNotEqual(app_name, "", "the configuration names the app")
                self.assertEqual(
                    parameters, "starlette_app",
                    f"rxconfig.py ({case}) must leave the proxy taking the Starlette app, not the Reflex app",
                )

    # -- 1.382 ---------------------------------------------------------
    def test_1_382_one_port_answers_the_backend_routes_and_proxies_every_page(self) -> None:
        """
        Scenario 1.382: run the way Reflex runs it, one port serves the backend and the pages.
        Given: a frontend server on its own port, a single-port configuration,
               and Reflex Enterprise's proxy as it ships, once rxconfig.py has
               built lex-app's sign-in plugin.
        When:  Reflex Enterprise registers the proxy, as it does when it builds
               the app; Reflex runs its lifespan tasks for the backend's
               Starlette app; and a browser asks the backend's port for
               ``/ping`` and for a page.
        Then:  ``/ping`` is the backend's own answer, and the page -- its path
               and query string -- is the frontend server's.
        """
        # In a directory of its own: Reflex Enterprise's registration notes its session in `.web/` under the cwd.
        with tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), \
                _frontend_server() as frontend_port, _shipped_proxy(), _clean_reflex_env():
            self.assertEqual(_parameters(proxy.proxy_middleware), ["app"], "the proxy as reflex-enterprise ships it")
            lex_auth_plugin()

            config = rxe.Config(
                app_name="single_port_probe", use_single_port=True,
                frontend_port=frontend_port, backend_port=frontend_port + 1,
            )
            reflex_app = LifespanMixin()
            with patch("reflex_enterprise.app.get_config", return_value=config), \
                    patch("reflex_enterprise.proxy.get_config", return_value=config):
                AppEnterprise._verify_and_setup_proxy(reflex_app)
                self.assertEqual(
                    len(reflex_app.get_lifespan_tasks()), 1, "single-port mode registers its proxy as a lifespan task",
                )
                backend = Starlette(
                    routes=[Route("/ping", lambda request: PlainTextResponse("pong"))],
                    lifespan=reflex_app._run_lifespan_tasks,
                )
                with TestClient(backend) as browser:
                    ping = browser.get("/ping")
                    page = browser.get("/funds/42?tab=history")

        self.assertEqual((ping.status_code, ping.text), (200, "pong"), "a backend route is answered by the backend")
        self.assertEqual(
            (page.status_code, page.text), (200, "frontend:/funds/42?tab=history"),
            "every other path must reach the frontend server through the backend's port",
        )

    # -- 1.383 ---------------------------------------------------------
    def test_1_383_repairing_again_or_a_proxy_that_needs_no_repair_changes_nothing(self) -> None:
        """
        Scenario 1.383: the repair applies once, and only to a proxy that needs it.
        Given: the proxy as it ships, already repaired once; and proxies that
               need no repair -- one taking ``starlette_app`` as a fixed Reflex
               Enterprise would, one taking both names, and the stand-in Reflex
               Enterprise defines without asgiproxy, which names none.
        When:  ``lex_auth_plugin()`` builds the sign-in plugin again, as a
               reloaded configuration does.
        Then:  each proxy is left exactly as it was.
        """

        @contextlib.asynccontextmanager
        async def fixed(starlette_app):
            yield

        @contextlib.asynccontextmanager
        async def both(app, starlette_app):
            yield

        @contextlib.asynccontextmanager
        async def stand_in(*args, **kwargs):
            yield

        with _shipped_proxy():
            lex_auth_plugin()
            repaired = proxy.proxy_middleware
            self.assertEqual(_parameters(repaired), ["starlette_app"], "the first build repairs the proxy")
            lex_auth_plugin()
            self.assertIs(proxy.proxy_middleware, repaired, "a second build must not wrap the repair again")

            for name, upstream in {"fixed": fixed, "both": both, "stand-in": stand_in}.items():
                with self.subTest(proxy=name), patch.object(proxy, "proxy_middleware", upstream):
                    lex_auth_plugin()
                    self.assertIs(proxy.proxy_middleware, upstream, f"a {name} proxy needs no repair")
