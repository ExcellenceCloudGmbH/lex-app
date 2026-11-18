"""Reflex dashboards without Reflex Enterprise: nothing imports it, and lex-app's own one-port proxy.

Intent: lex-app's Reflex dashboards run on plain Reflex and RFDS's open path, in every run mode,
with no Reflex account -- so nothing in lex-app may import ``reflex_enterprise`` or
``rfds.enterprise``, and the requirements may not pull them in. What Reflex Enterprise did besides
sign-in, lex-app does itself: a development run behind the one port a pod exposes
(``REFLEX_USE_SINGLE_PORT``) has the backend pass every other request -- HTTP, and the frontend
dev server's hot-reload WebSocket -- to the frontend server.

A regression here is a dashboard that asks for ``reflex login`` again (or will not start
without a paid tier), or a pod whose pages all 404 because nothing passes them on.

Cluster 1by -- scenarios 1.736-1.739. Type: U.
Covers: requirements.txt (reflex, rfds), lex/lex_app/reflex/ (every module, for its imports),
lex/lex_app/reflex/devproxy.py (enabled, FrontendProxy, install), lex/lex_app/reflex/app.py
(create_app installs the proxy).
Run: python -m lex pytest lex/test_project/tests/init/test_1by_reflex_without_enterprise.py -v
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import httpx
import pytest
from starlette.testclient import TestClient

import lex
from lex.lex_app.reflex import devproxy

pytestmark = pytest.mark.init

_CHECKOUT = Path(lex.__file__).resolve().parents[1]

#: A child that refuses Reflex Enterprise outright, then imports every Reflex module of lex-app.
_PROBE = r"""
import importlib, sys
class _Refuse:
    def find_spec(self, name, path=None, target=None):
        if name == "reflex_enterprise" or name.startswith(("reflex_enterprise.", "rfds.enterprise")):
            raise ImportError(f"{name} is not allowed")
        return None
sys.meta_path.insert(0, _Refuse())
from lex.lex_app.reflex.config import lex_config
config = lex_config()
for name in ("access", "auth", "app", "dashboards", "data", "design", "devproxy", "embed", "host",
             "scaffold", "session", "shell", "django_orm"):
    importlib.import_module("lex.lex_app.reflex." + name)
print("OK", type(config).__module__, sorted(m for m in sys.modules if "enterprise" in m))
"""


class TestCluster01by_NoEnterprise(TestCase):
    """1.736: nothing lex-app ships needs Reflex Enterprise."""

    # -- 1.736 ---------------------------------------------------------
    def test_1_736_nothing_imports_reflex_enterprise_or_requires_it(self) -> None:
        """
        Scenario 1.736: plain Reflex and core RFDS are all lex-app needs.
        Given: an interpreter where importing reflex_enterprise or rfds.enterprise fails.
        When:  the config is built and every module of lex.lex_app.reflex is imported; and the
               requirements are read.
        Then:  all of it works, the config is Reflex's own, no Enterprise module is loaded;
               the requirements pin plain `reflex` and `rfds` without its enterprise extra, and
               no `reflex-enterprise`.
        """
        env = dict(os.environ, DJANGO_SETTINGS_MODULE="lex_app.settings")
        env["PYTHONPATH"] = os.pathsep.join(p for p in (str(_CHECKOUT), env.get("PYTHONPATH")) if p)
        result = subprocess.run([sys.executable, "-c", _PROBE], capture_output=True, text=True, timeout=300,
                                env=env, cwd=str(_CHECKOUT))
        self.assertEqual(result.returncode, 0, result.stderr[-3000:])
        last = result.stdout.strip().splitlines()[-1]
        self.assertTrue(last.startswith("OK reflex"), last)
        self.assertTrue(last.endswith("[]"), f"an Enterprise module was loaded: {last}")

        requirements = [line.split("#")[0].strip().lower() for line in
                        (_CHECKOUT / "requirements.txt").read_text().splitlines()]
        self.assertFalse([r for r in requirements if r.startswith("reflex-enterprise")])
        self.assertIn("rfds==1.0.0", requirements, "core RFDS, no [enterprise] extra")
        self.assertTrue([r for r in requirements if r.startswith("reflex==")])


class TestCluster01by_Switch(TestCase):
    """1.737: when the backend passes pages on."""

    # -- 1.737 ---------------------------------------------------------
    def test_1_737_the_proxy_runs_only_for_a_one_port_development_run(self) -> None:
        """
        Scenario 1.737: REFLEX_USE_SINGLE_PORT turns the proxy on, in development only.
        Given: the switch unset, set, set in production mode, and set for a backend-only run.
        When:  lex-app decides, and create_app installs it.
        Then:  only "set, development, not backend-only" proxies -- production serves both on
               one port itself, a backend-only run has no frontend to reach; when it does,
               a lifespan task mounts the proxy, and otherwise nothing is registered.
        """
        def decide(env, prod=False):
            with patch.dict(os.environ, env, clear=False), patch("reflex.utils.exec.is_prod_mode", return_value=prod):
                for name in ("REFLEX_USE_SINGLE_PORT", "REFLEX_BACKEND_ONLY"):
                    if name not in env:
                        os.environ.pop(name, None)
                return devproxy.enabled()

        self.assertFalse(decide({}))
        self.assertTrue(decide({"REFLEX_USE_SINGLE_PORT": "true"}))
        self.assertFalse(decide({"REFLEX_USE_SINGLE_PORT": "true"}, prod=True))
        self.assertFalse(decide({"REFLEX_USE_SINGLE_PORT": "1", "REFLEX_BACKEND_ONLY": "true"}))

        registered = []
        app = SimpleNamespace(register_lifespan_task=registered.append)
        with patch.object(devproxy, "enabled", return_value=False):
            devproxy.install(app)
        self.assertEqual(registered, [])
        with patch.object(devproxy, "enabled", return_value=True):
            devproxy.install(app)
        self.assertEqual(registered, [devproxy._proxy_lifespan])


class TestCluster01by_Http(TestCase):
    """1.738: HTTP through the proxy."""

    def _proxy(self, handler):
        proxy = devproxy.FrontendProxy("http://localhost:3000")
        proxy._client = httpx.AsyncClient(base_url=proxy.upstream, transport=httpx.MockTransport(handler))
        return proxy

    # -- 1.738 ---------------------------------------------------------
    def test_1_738_pages_pass_through_to_the_frontend_server(self) -> None:
        """
        Scenario 1.738: an HTTP request the backend does not answer reaches the frontend.
        Given: the proxy in front of a frontend server.
        When:  a page is requested with a query and a body, and the frontend is not up yet.
        Then:  method, path, query and body pass on, the Host header does not (the upstream's
               own applies) and hop-by-hop headers are dropped; status, headers and body come
               back -- a gzipped body decoded, without its Content-Encoding; a frontend still
               starting is retried, one that never answers is a 502 after the retries.
        """
        import gzip
        seen = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            return httpx.Response(201, headers={"x-from": "frontend", "content-type": "text/html"}, content=b"<p>page</p>")

        client = TestClient(self._proxy(handler), base_url="http://localhost:8502")
        response = client.post("/records?page=2", content=b"body",
                               headers={"proxy-connection": "keep-alive", "te": "trailers", "x-keep": "1"})
        self.assertEqual((response.status_code, response.text, response.headers["x-from"]), (201, "<p>page</p>", "frontend"))
        request = seen[0]
        self.assertEqual((request.method, request.url.path, request.url.query, request.content),
                         ("POST", "/records", b"page=2", b"body"))
        self.assertEqual(request.headers["host"], "localhost:3000")
        self.assertEqual(request.headers["x-keep"], "1")
        self.assertNotIn("proxy-connection", request.headers, "hop-by-hop headers stay on this hop")
        self.assertNotIn("te", request.headers)

        def zipped(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, headers={"content-encoding": "gzip"}, content=gzip.compress(b"js bundle"))

        unzipped = TestClient(self._proxy(zipped), base_url="http://localhost:8502").get("/app.js")
        self.assertEqual(unzipped.content, b"js bundle")
        self.assertNotIn("content-encoding", unzipped.headers)

        attempts = []

        def starting(request: httpx.Request) -> httpx.Response:
            attempts.append(request.content)
            if len(attempts) < 3:
                raise httpx.ConnectError("refused", request=request)
            return httpx.Response(200, content=b"up")

        with patch.object(devproxy, "RETRY_DELAY", 0.0):
            late = TestClient(self._proxy(starting), base_url="http://localhost:8502").post("/x", content=b"payload")
        self.assertEqual((late.status_code, late.text, attempts), (200, "up", [b"payload"] * 3),
                         "every retry carries the body")

        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        with patch.object(devproxy, "CONNECT_RETRIES", 3), patch.object(devproxy, "RETRY_DELAY", 0.0):
            failed = TestClient(self._proxy(down), base_url="http://localhost:8502").get("/")
        self.assertEqual(failed.status_code, 502)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class TestCluster01by_WebSocket(TestCase):
    """1.739: the dev server's hot-reload socket through the proxy."""

    # -- 1.739 ---------------------------------------------------------
    def test_1_739_the_hot_reload_socket_passes_through_with_its_subprotocol(self) -> None:
        """
        Scenario 1.739: a WebSocket the backend does not answer reaches the frontend server.
        Given: a frontend dev server answering WebSockets with the `vite-hmr` subprotocol.
        When:  the browser opens one through the proxy, sends text and bytes, and closes it.
        Then:  it is accepted with the upstream's subprotocol; messages go both ways unchanged;
               a frontend that is not there refuses the socket instead of hanging.
        """
        from websockets.sync.server import serve

        port = _free_port()

        def echo(ws):
            for message in ws:
                ws.send(message)

        server = serve(echo, "127.0.0.1", port, subprotocols=["vite-hmr"])
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.shutdown)

        # Mounted on the backend, the proxy never sees a lifespan: the client opens no context.
        client = TestClient(devproxy.FrontendProxy(f"http://127.0.0.1:{port}"), base_url="http://localhost:8502")
        with client.websocket_connect("/", subprotocols=["vite-hmr"]) as ws:
            self.assertEqual(ws.accepted_subprotocol, "vite-hmr")
            ws.send_text("update")
            self.assertEqual(ws.receive_text(), "update")
            ws.send_bytes(b"\x01\x02")
            self.assertEqual(ws.receive_bytes(), b"\x01\x02")

        gone = TestClient(devproxy.FrontendProxy(f"http://127.0.0.1:{_free_port()}"), base_url="http://localhost:8502")
        with self.assertRaises(Exception):
            with gone.websocket_connect("/"):
                pass
