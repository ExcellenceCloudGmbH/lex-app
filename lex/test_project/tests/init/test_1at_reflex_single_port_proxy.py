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
``app``. Handed the Starlette app, it mounts on the outermost one, where Reflex
reaches its backend through a catch-all mount -- behind an ``api_transformer``
that wraps the backend in middleware, as RFDS's security headers do, the proxy
is never reached. lex-app mounts it on the Reflex backend itself, so:

* a project's ``rxconfig.py`` repairs the proxy as Reflex loads it --
  ``lex_config()`` in the one ``lex reflex`` writes, ``lex_auth_plugin()`` in a
  hand-written one -- and the configuration is still the project's;
* run the way Reflex runs it, with or without middleware around the backend,
  the backend's port answers the backend's routes itself and every other path,
  query string included, from the frontend server;
* repairing again changes nothing, and the stand-in Reflex Enterprise defines
  without asgiproxy is left exactly as it is.

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
    "      getattr(proxy.proxy_middleware, 'lex_mounts_on_backend', False))\n"
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
               proxy is lex-app's, which mounts on the Reflex backend.
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
                app_name, loaded_module, repaired = result.stdout.strip().splitlines()[-1].split()
                self.assertEqual(
                    loaded_module, app_module, f"Reflex must still load the project's configuration ({case})",
                )
                self.assertNotEqual(app_name, "", "the configuration names the app")
                self.assertEqual(repaired, "True", f"rxconfig.py ({case}) must leave lex-app's proxy in place")

    # -- 1.382 ---------------------------------------------------------
    def test_1_382_one_port_answers_the_backend_routes_and_proxies_every_page(self) -> None:
        """
        Scenario 1.382: run the way Reflex runs it, one port serves the backend and the pages.
        Given: a frontend server on its own port, a single-port configuration,
               Reflex Enterprise's proxy as it ships once rxconfig.py has built
               lex-app's sign-in plugin, and the backend assembled as Reflex
               assembles it -- mounted under a catch-all in the outermost app,
               bare or wrapped in ASGI middleware by an ``api_transformer``.
        When:  Reflex Enterprise registers the proxy, as it does when it builds
               the app; Reflex runs its lifespan tasks; and a browser asks the
               backend's port for ``/ping`` and for a page.
        Then:  ``/ping`` is the backend's own answer, and the page -- its path
               and query string -- is the frontend server's, through the
               middleware when there is some.
        """

        def wrapped(inner):
            async def middleware(scope, receive, send):
                if scope["type"] != "http":
                    return await inner(scope, receive, send)

                async def marked(message):
                    if message["type"] == "http.response.start":
                        message = {**message, "headers": [*message.get("headers", []), (b"x-wrapped", b"yes")]}
                    await send(message)

                await inner(scope, receive, marked)

            return middleware

        for case, transform in {"bare": None, "wrapped in middleware": wrapped}.items():
            # In a directory of its own: Reflex Enterprise's registration notes its session in `.web/` under the cwd.
            with self.subTest(backend=case), tempfile.TemporaryDirectory() as tmp, contextlib.chdir(tmp), \
                    _frontend_server() as frontend_port, _shipped_proxy(), _clean_reflex_env():
                self.assertEqual(_parameters(proxy.proxy_middleware), ["app"], "the proxy as it ships")
                lex_auth_plugin()

                config = rxe.Config(
                    app_name="single_port_probe", use_single_port=True,
                    frontend_port=frontend_port, backend_port=frontend_port + 1,
                )
                reflex_app = LifespanMixin()
                reflex_app._api = Starlette(routes=[Route("/ping", lambda request: PlainTextResponse("pong"))])
                with patch("reflex_enterprise.app.get_config", return_value=config), \
                        patch("reflex_enterprise.proxy.get_config", return_value=config):
                    AppEnterprise._verify_and_setup_proxy(reflex_app)
                    self.assertEqual(len(reflex_app.get_lifespan_tasks()), 1, "one lifespan task: the proxy")
                    # What `reflex.app.App.__call__` builds: the backend, transformed, under a catch-all mount.
                    backend = reflex_app._api if transform is None else transform(reflex_app._api)
                    outermost = Starlette(lifespan=reflex_app._run_lifespan_tasks)
                    outermost.mount("", backend)
                    with TestClient(outermost) as browser:
                        ping = browser.get("/ping")
                        page = browser.get("/funds/42?tab=history")

                self.assertEqual((ping.status_code, ping.text), (200, "pong"), "a backend route is the backend's")
                self.assertEqual(
                    (page.status_code, page.text), (200, "frontend:/funds/42?tab=history"),
                    f"every other path must reach the frontend server through the backend's port ({case})",
                )
                if transform is not None:
                    self.assertEqual(page.headers.get("x-wrapped"), "yes", "a proxied page passes the middleware")

    # -- 1.383 ---------------------------------------------------------
    def test_1_383_repairing_again_or_a_proxy_that_needs_no_repair_changes_nothing(self) -> None:
        """
        Scenario 1.383: the repair applies once, and not to the stand-in.
        Given: the proxy as it ships, already repaired once; and the stand-in
               Reflex Enterprise defines without asgiproxy, which names no
               parameter.
        When:  ``lex_auth_plugin()`` builds the sign-in plugin again, as a
               reloaded configuration does.
        Then:  each proxy is left exactly as it was.
        """

        @contextlib.asynccontextmanager
        async def stand_in(*args, **kwargs):
            yield

        with _shipped_proxy():
            lex_auth_plugin()
            repaired = proxy.proxy_middleware
            self.assertTrue(getattr(repaired, "lex_mounts_on_backend", False), "the first build repairs the proxy")
            self.assertEqual(_parameters(repaired), ["app"], "it takes the Reflex app, whose backend it mounts on")
            lex_auth_plugin()
            self.assertIs(proxy.proxy_middleware, repaired, "a second build must not wrap the repair again")

            with patch.object(proxy, "proxy_middleware", stand_in):
                lex_auth_plugin()
                self.assertIs(proxy.proxy_middleware, stand_in, "the stand-in without asgiproxy needs no repair")
