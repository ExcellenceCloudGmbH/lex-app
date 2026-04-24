"""
Cluster 1h: ``lex init`` bootstrap flow — URL building + HTTP polling.

Intent
------

When a customer runs ``lex init`` for the very first time and their
``.env`` is missing the Keycloak credentials, the command switches
into **bootstrap mode**: it opens the instance-controller web UI,
starts a local callback server, and polls until the credentials
arrive. Sub-cluster 1d covered the ``.env`` / state-file / missing-env
helpers; this sub-cluster closes the remaining bootstrap surface:

* :func:`build_instance_controller_url` — assembles the controller URL
  with the callback, project slug, and state token in the query-string.
* :func:`fetch_bootstrap_state` — single polling request; returns the
  payload dict on success, ``{"cancelled": True}`` on cancel, ``None``
  while pending, and raises on every non-2xx / non-JSON response.
* :func:`poll_bootstrap_status` — fail-fast polling loop; aborts on
  cancel / timeout / HTTP error, never silently retries.
* :func:`wait_for_keycloak_setup_v2` — the loop that couples the env
  check with controller polling and returns the credentials dict
  (falling through to env vars once they arrive out-of-band).

All scenarios are **network-free**: ``requests.get`` is patched with a
``MagicMock``; the real HTTP stack never runs.

Scenario numbering extends ``docs/test-plan/test-clusters.md`` —
sub-cluster 1h picks up at **1.47**.
"""

from __future__ import annotations

import unittest
from unittest import TestCase, mock

from django.core.management.base import CommandError

from lex.lex_app.management.commands import init as init_module
from lex.lex_app.management.commands.init import (
    build_instance_controller_url,
    fetch_bootstrap_state,
    poll_bootstrap_status,
    wait_for_keycloak_setup_v2,
)


def _http_response(status_code=200, json_body=None, raise_for_status=False, text=""):
    """Build a ``requests``-shaped response stand-in."""
    resp = mock.MagicMock()
    resp.status_code = status_code
    resp.text = text or (str(json_body) if json_body is not None else "")
    if raise_for_status:
        resp.raise_for_status.side_effect = Exception(f"HTTP {status_code}")
    else:
        resp.raise_for_status.return_value = None
    if json_body is None:
        resp.json.side_effect = ValueError("not JSON")
    else:
        resp.json.return_value = json_body
    return resp


# ---------------------------------------------------------------------
# 1.47 — build_instance_controller_url
# ---------------------------------------------------------------------
class TestCluster01h_BuildControllerUrl(TestCase):
    """``build_instance_controller_url`` assembles the bootstrap URL
    with callback + state token in the query-string."""

    def test_1_47_url_carries_all_required_query_params(self):
        """Scenario 1.47: the URL starts with the configured base and
        carries every parameter the controller UI reads off the query
        string — ``state``, ``callback``, ``flow``, ``project``.
        """
        with mock.patch.object(
            init_module.settings,
            "INSTANCE_CONTROLLER_BASE_URL",
            "https://controller.example.com/",
            create=True,
        ), mock.patch.object(
            init_module.settings, "LEX_PROJECT_SLUG", "my-customer", create=True,
        ):
            url = build_instance_controller_url(
                state="abc-123",
                callback_url="http://127.0.0.1:9001/callback",
            )

        self.assertTrue(
            url.startswith("https://controller.example.com/lex/keycloak-bootstrap?"),
            f"URL must be anchored on the configured base; got {url!r}",
        )
        # Trailing slash on the base was stripped before concatenation.
        self.assertNotIn("//lex/keycloak-bootstrap", url)
        self.assertIn("state=abc-123", url)
        self.assertIn("callback=http%3A%2F%2F127.0.0.1%3A9001%2Fcallback", url)
        self.assertIn("flow=keycloak-client-bootstrap", url)
        self.assertIn("project=my-customer", url)

    def test_1_47b_missing_base_url_raises(self):
        """Scenario 1.47b: without ``INSTANCE_CONTROLLER_BASE_URL``
        we abort cleanly — the customer sees a specific error instead
        of a mysterious malformed URL."""
        with mock.patch.object(
            init_module.settings, "INSTANCE_CONTROLLER_BASE_URL", "", create=True,
        ):
            with self.assertRaises(CommandError):
                build_instance_controller_url("s", "cb")


# ---------------------------------------------------------------------
# 1.48 — fetch_bootstrap_state
# ---------------------------------------------------------------------
class TestCluster01h_FetchBootstrapState(TestCase):
    """``fetch_bootstrap_state`` — one polling request, fail-fast on
    every error path."""

    def setUp(self):
        self._base_patch = mock.patch.object(
            init_module.settings,
            "INSTANCE_CONTROLLER_BASE_URL",
            "https://controller.example.com",
            create=True,
        )
        self._base_patch.start()
        self.addCleanup(self._base_patch.stop)

    def test_1_48_done_status_returns_payload(self):
        """Scenario 1.48: ``status=done`` + payload → payload returned."""
        payload = {"KEYCLOAK_URL": "https://kc.example", "KEYCLOAK_REALM": "demo"}
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, {"status": "done", "payload": payload}),
        ):
            result = fetch_bootstrap_state("state-xyz")

        self.assertEqual(result, payload)

    def test_1_48b_cancelled_status_returns_sentinel(self):
        """Scenario 1.48b: ``status=cancelled`` returns ``{"cancelled": True}``
        so the caller can distinguish cancel from pending."""
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, {"status": "cancelled"}),
        ):
            self.assertEqual(
                fetch_bootstrap_state("state-xyz"),
                {"cancelled": True},
            )

    def test_1_48c_pending_status_returns_none(self):
        """Scenario 1.48c: still pending → ``None`` so callers keep polling."""
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, {"status": "pending"}),
        ):
            self.assertIsNone(fetch_bootstrap_state("state-xyz"))

    def test_1_48d_non_2xx_raises(self):
        """Scenario 1.48d: a 4xx/5xx response raises — no silent retry.

        Operators must see auth failures against the controller
        instead of an init command that "hangs"."""
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(500, None, raise_for_status=True, text="boom"),
        ):
            with self.assertRaises(CommandError):
                fetch_bootstrap_state("state-xyz")

    def test_1_48e_non_json_body_raises(self):
        """Scenario 1.48e: a 200 with a non-JSON body is a protocol
        violation and must abort — returning ``None`` would be indistinguishable
        from "still pending" and produce an infinite wait."""
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, None, text="<html>login</html>"),
        ):
            with self.assertRaises(CommandError):
                fetch_bootstrap_state("state-xyz")


# ---------------------------------------------------------------------
# 1.49 — poll_bootstrap_status
# ---------------------------------------------------------------------
class TestCluster01h_PollBootstrapStatus(TestCase):
    """``poll_bootstrap_status`` — loops until done / cancelled /
    timeout, fail-fast on every error."""

    def setUp(self):
        self._base_patch = mock.patch.object(
            init_module.settings,
            "INSTANCE_CONTROLLER_BASE_URL",
            "https://controller.example.com/",
            create=True,
        )
        self._base_patch.start()
        self.addCleanup(self._base_patch.stop)

        # Never actually sleep.
        self._sleep_patch = mock.patch.object(init_module.time, "sleep", lambda *_a, **_kw: None)
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)

    def test_1_49_returns_payload_on_done(self):
        """Scenario 1.49: loop yields the payload on the first ``done``
        response, even after earlier pending responses."""
        payload = {"KEYCLOAK_URL": "https://kc", "KEYCLOAK_REALM": "r"}
        responses = [
            _http_response(200, {"status": "pending"}),
            _http_response(200, {"status": "pending"}),
            _http_response(200, {"status": "done", "payload": payload}),
        ]
        with mock.patch.object(
            init_module.requests, "get", side_effect=responses,
        ):
            self.assertEqual(poll_bootstrap_status("s"), payload)

    def test_1_49b_cancelled_raises(self):
        """Scenario 1.49b: cancel is a hard abort."""
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, {"status": "cancelled"}),
        ):
            with self.assertRaises(CommandError) as ctx:
                poll_bootstrap_status("s")
            self.assertIn("cancelled", str(ctx.exception).lower())

    def test_1_49c_done_without_payload_raises(self):
        """Scenario 1.49c: a ``done`` with no payload is a protocol
        violation — the controller guaranteed credentials but delivered
        nothing, so we cannot continue.
        """
        with mock.patch.object(
            init_module.requests, "get",
            return_value=_http_response(200, {"status": "done"}),
        ):
            with self.assertRaises(CommandError) as ctx:
                poll_bootstrap_status("s")
            self.assertIn("payload", str(ctx.exception).lower())

    def test_1_49d_timeout_raises(self):
        """Scenario 1.49d: once the deadline passes we raise — the
        init command refuses to wait forever."""
        # Patch time.time so the timeout trips on the second check.
        clock = iter([0, 0, 100_000])  # start, first loop, second loop
        with mock.patch.object(init_module.time, "time", lambda: next(clock)), \
                mock.patch.object(
                    init_module.requests, "get",
                    return_value=_http_response(200, {"status": "pending"}),
                ):
            with self.assertRaises(CommandError) as ctx:
                poll_bootstrap_status("s", timeout_seconds=1, poll_interval=0)
            self.assertIn("Timed out", str(ctx.exception))


# ---------------------------------------------------------------------
# 1.50 — wait_for_keycloak_setup_v2
# ---------------------------------------------------------------------
class TestCluster01h_WaitForKeycloakSetupV2(TestCase):
    """``wait_for_keycloak_setup_v2`` couples env-var detection with
    controller polling."""

    def setUp(self):
        self._sleep_patch = mock.patch.object(init_module.time, "sleep", lambda *_a, **_kw: None)
        self._sleep_patch.start()
        self.addCleanup(self._sleep_patch.stop)

    def test_1_50_env_already_set_returns_env_dict(self):
        """Scenario 1.50: if env vars arrive out-of-band (e.g. the
        customer ran the bootstrap in another terminal), we skip the
        controller entirely and return the env-sourced credentials."""
        fake_env = {
            "KEYCLOAK_URL": "https://kc",
            "KEYCLOAK_REALM": "demo",
            "OIDC_RP_CLIENT_ID": "cid",
            "OIDC_RP_CLIENT_SECRET": "sec",
            "OIDC_RP_CLIENT_UUID": "uuid",
        }
        with mock.patch.object(
            init_module, "get_missing_keycloak_env", return_value=[],
        ), mock.patch.dict(init_module.__dict__["__builtins__"].__dict__ if hasattr(init_module.__dict__.get("__builtins__"), "__dict__") else {}, {}, clear=False), \
                mock.patch.dict("os.environ", fake_env, clear=False):
            result = wait_for_keycloak_setup_v2("s")

        # Values pulled straight from env — the v2 helper re-reads os.environ.
        self.assertEqual(result["KEYCLOAK_URL"], "https://kc")
        self.assertEqual(result["KEYCLOAK_REALM"], "demo")
        self.assertEqual(result["OIDC_RP_CLIENT_ID"], "cid")
        self.assertEqual(result["OIDC_RP_CLIENT_SECRET"], "sec")
        self.assertEqual(result["OIDC_RP_CLIENT_UUID"], "uuid")

    def test_1_50b_cancelled_from_controller_raises(self):
        """Scenario 1.50b: a controller ``cancelled`` response aborts
        the whole init — the customer explicitly declined."""
        with mock.patch.object(
            init_module, "get_missing_keycloak_env", return_value=["KEYCLOAK_URL"],
        ), mock.patch.object(
            init_module, "fetch_bootstrap_state",
            return_value={"cancelled": True},
        ):
            with self.assertRaises(CommandError) as ctx:
                wait_for_keycloak_setup_v2("s")
            self.assertIn("cancelled", str(ctx.exception).lower())

    def test_1_50c_payload_from_controller_is_returned(self):
        """Scenario 1.50c: once the controller returns a payload, it
        is handed back as-is — no env-var re-read, because the caller
        is about to write these values to ``.env``."""
        payload = {
            "KEYCLOAK_URL": "https://new-kc",
            "KEYCLOAK_REALM": "new-realm",
            "OIDC_RP_CLIENT_ID": "new-cid",
            "OIDC_RP_CLIENT_SECRET": "new-sec",
            "OIDC_RP_CLIENT_UUID": "new-uuid",
        }
        with mock.patch.object(
            init_module, "get_missing_keycloak_env", return_value=["KEYCLOAK_URL"],
        ), mock.patch.object(
            init_module, "fetch_bootstrap_state", return_value=payload,
        ):
            result = wait_for_keycloak_setup_v2("s")

        self.assertEqual(result, payload)

    def test_1_50d_timeout_raises(self):
        """Scenario 1.50d: if neither env nor controller produce
        credentials before the deadline, we abort."""
        clock = iter([0, 0, 100_000])
        with mock.patch.object(
            init_module, "get_missing_keycloak_env", return_value=["KEYCLOAK_URL"],
        ), mock.patch.object(
            init_module, "fetch_bootstrap_state", return_value=None,
        ), mock.patch.object(init_module.time, "time", lambda: next(clock)):
            with self.assertRaises(CommandError) as ctx:
                wait_for_keycloak_setup_v2("s", timeout_seconds=1, poll_interval=0)
            self.assertIn("Timed out", str(ctx.exception))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

