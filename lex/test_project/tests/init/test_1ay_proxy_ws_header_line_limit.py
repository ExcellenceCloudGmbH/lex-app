"""A WebSocket handshake with one long header line must still reach Streamlit.

Intent: since Streamlit 1.66 its server is uvicorn on the ``websockets-sansio``
backend, which caps each request header line at ``websockets.http11``'s
``MAX_LINE_LENGTH`` (8192 bytes) and, past it, never answers. In production
(claim-validation, prod-1527) that showed as an endless loop of
``Upstream websocket connection failed ... timed out during opening handshake``
while every plain HTTP request to Streamlit returned 200: the app shell loaded
and then never connected. The ``Cookie`` line the proxy forwarded -- carrying
its own ``st_access`` JWT and session cookie -- was long enough to cross 8 KB.

Two defences, both covered here:

* the proxy raises the header-line limit for the whole process on import;
* ``ws_proxy`` stops forwarding the proxy's own cookies upstream.

Cluster 1ay. Type: U + I.
Covers: lex/proxy.py (_raise_websockets_line_limit, _strip_proxy_cookies).
Run: python -m lex pytest lex/test_project/tests/init/test_1ay_proxy_ws_header_line_limit.py -v
"""

from __future__ import annotations

import asyncio
import os
import socket
from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

import lex.proxy as proxy

pytestmark = pytest.mark.init


class ProxyCookieStrippingTests(SimpleTestCase):
    def test_proxy_owned_cookies_are_dropped_and_others_kept(self):
        headers = [
            ("user-agent", "x"),
            ("cookie", "st_access=eyJ.big.jwt; _streamlit_xsrf=abc; session=signed; theme=dark"),
        ]
        out = proxy._strip_proxy_cookies(headers)
        self.assertEqual(out, [("user-agent", "x"), ("cookie", "_streamlit_xsrf=abc; theme=dark")])

    def test_cookie_header_is_omitted_when_nothing_is_left(self):
        out = proxy._strip_proxy_cookies([("Cookie", "st_access=a; session=b")])
        self.assertEqual(out, [])

    def test_cookie_name_match_is_exact(self):
        out = proxy._strip_proxy_cookies([("cookie", "sessionid=keep; my_st_access=keep")])
        self.assertEqual(out, [("cookie", "sessionid=keep; my_st_access=keep")])


class WebsocketsLineLimitTests(SimpleTestCase):
    def test_limit_is_raised_on_import(self):
        from websockets import http11

        self.assertGreaterEqual(http11.MAX_LINE_LENGTH, proxy.WS_MIN_MAX_LINE_LENGTH)

    def test_explicit_env_value_is_respected(self):
        from websockets import http11

        with patch.object(http11, "MAX_LINE_LENGTH", 8192), patch.dict(
            os.environ, {"WEBSOCKETS_MAX_LINE_LENGTH": "8192"}
        ):
            proxy._raise_websockets_line_limit()
            self.assertEqual(http11.MAX_LINE_LENGTH, 8192)

    def test_long_header_line_completes_handshake_on_sansio_server(self):
        """The production failure, end to end: uvicorn sansio + a 16 KB Cookie."""
        import uvicorn
        from starlette.applications import Starlette
        from starlette.routing import WebSocketRoute
        from websockets.asyncio.client import connect

        async def endpoint(ws):
            await ws.accept(subprotocol="streamlit")
            await ws.close()

        app = Starlette(routes=[WebSocketRoute("/_stcore/stream", endpoint)])

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        async def run() -> str:
            config = uvicorn.Config(app, host="127.0.0.1", port=port, ws="websockets-sansio", log_level="warning")
            server = uvicorn.Server(config)
            task = asyncio.create_task(server.serve())
            try:
                while not server.started:
                    await asyncio.sleep(0.02)
                async with connect(
                    f"ws://127.0.0.1:{port}/_stcore/stream",
                    subprotocols=["streamlit"],
                    additional_headers=[("Cookie", "a=" + "x" * 16_000)],
                    open_timeout=5,
                    proxy=None,
                ) as ws:
                    return ws.subprotocol
            finally:
                server.should_exit = True
                await task

        self.assertEqual(asyncio.run(run()), "streamlit")
