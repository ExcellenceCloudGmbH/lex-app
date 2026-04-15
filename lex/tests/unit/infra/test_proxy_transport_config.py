from unittest import TestCase
from unittest.mock import patch

import httpx

import lex.proxy as proxy


class ProxyTransportConfigTests(TestCase):
    def test_upstream_http_client_bypasses_system_proxy_by_default(self):
        timeout = httpx.Timeout(30.0)

        kwargs = proxy._upstream_http_client_kwargs(timeout)

        self.assertIs(kwargs["timeout"], timeout)
        self.assertFalse(kwargs["trust_env"])
        self.assertFalse(kwargs["follow_redirects"])

    def test_upstream_http_client_can_opt_into_system_proxy(self):
        timeout = httpx.Timeout(30.0)

        with patch.object(proxy, "UPSTREAM_USE_SYSTEM_PROXY", True):
            kwargs = proxy._upstream_http_client_kwargs(timeout)

        self.assertTrue(kwargs["trust_env"])

    def test_upstream_ws_disables_system_proxy_when_supported(self):
        with patch.object(proxy, "WS_HAS_PROXY", True), patch.object(
            proxy, "UPSTREAM_USE_SYSTEM_PROXY", False
        ):
            kwargs = proxy._build_upstream_ws_kwargs({})

        self.assertIsNone(kwargs["proxy"])

    def test_upstream_ws_keeps_proxy_unset_when_opted_in(self):
        with patch.object(proxy, "WS_HAS_PROXY", True), patch.object(
            proxy, "UPSTREAM_USE_SYSTEM_PROXY", True
        ):
            kwargs = proxy._build_upstream_ws_kwargs({})

        self.assertNotIn("proxy", kwargs)
