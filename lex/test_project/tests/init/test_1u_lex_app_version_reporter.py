"""lex-app reports its installed version to the LEX Instance Controller on startup.

Intent: the IC frontend shows a "LEX APP VERSION" field in instance details so
operators can see which framework version is actually running in each customer
pod. The IC database value is unreliable because release Terraform operations
can fail silently (state-lock + UI auto-refresh) — leaving a stale or
incorrectly-bumped ``image_version`` while the customer pod still runs the
previous build. To fix that, the running customer instance is the authoritative
source: at server startup it pushes ``lex._version.__version__`` to IC over the
already-existing instance→IC HTTP channel (same env vars + ``Authorization:
Api-Key`` header the rest of ``lex.api.views.lex_api.LexAPI`` uses). If IC
never receives the report, its DB keeps the previous value (or empty for first
deploy) → the UI renders ``Unknown`` instead of misleading the operator.

Regressions matter because:

* a startup hook that **raises** turns "best-effort version reporting" into a
  pod-startup crash — the customer's whole instance fails to come up because we
  could not phone home a version string,
* a hook that **blocks** delays pod readiness, which directly impacts the
  customer's perceived uptime during every restart, and
* a hook that **re-pushes every request** (no once-per-process guard) DDoSes
  IC's report endpoint and floods the audit trail of which version is current.

Cluster 1u — scenarios 1.171 – 1.177. Type: U.
Covers: lex/lex_app/report_lex_app_version.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1u_lex_app_version_reporter.py -v
"""

from __future__ import annotations

import threading
import unittest
from unittest.mock import MagicMock, patch

import pytest
import requests
from django.test import SimpleTestCase

from lex.lex_app import report_lex_app_version as reporter
from lex.lex_app.report_lex_app_version import (
    _do_report,
    _reset_for_tests,
    report_lex_app_version_to_ic,
)

pytestmark = pytest.mark.init


# A full triple of env vars that simulates a deployed customer instance.
# Mirrors the env contract documented in lex.api.views.lex_api.LexAPI (the
# same envs `LexAPI.send_email` requires before it will fire).
_DEPLOYED_ENV = {
    "DEPLOYMENT_ENVIRONMENT": "prod",
    "DOMAIN_BASE": "example-instance.lit.example.com",
    "LEX_API_KEY": "test-api-key-deadbeef",
}


class TestCluster01u_LexAppVersionReporter(SimpleTestCase):
    """Cluster 1u: startup version reporter — gating, guard, and HTTP behaviour."""

    def setUp(self) -> None:
        super().setUp()
        # The reporter has a module-level once-per-process latch; each test
        # gets a clean slate so the gating semantics are tested in isolation.
        _reset_for_tests()

    def tearDown(self) -> None:
        _reset_for_tests()
        super().tearDown()

    # -- 1.171 ---------------------------------------------------------
    def test_1_171_not_deployed_skips_push(self) -> None:
        """
        Scenario 1.171: outside a deployed pod the reporter is a no-op.
        Given: ``DEPLOYMENT_ENVIRONMENT`` is unset (local dev, CI, pytest run)
        When: ``report_lex_app_version_to_ic()`` is called
        Then: it returns ``False`` and starts no background thread — the
              startup gate (same one ``LexAPI.send_email`` uses) protects
              local developers from accidentally pinging a production IC.
        """
        # Ensure none of the three env vars are present, then call.
        with patch.dict("os.environ", {}, clear=False):
            for key in _DEPLOYED_ENV:
                # patch.dict cannot easily "remove" a key it didn't add, so do
                # a defensive pop after entering the context.
                pass
            with patch.dict(
                "os.environ",
                {k: "" for k in _DEPLOYED_ENV},
                clear=False,
            ):
                with patch.object(reporter.threading, "Thread") as thread_cls:
                    started = report_lex_app_version_to_ic()

        self.assertFalse(
            started,
            "report_lex_app_version_to_ic must return False outside a deployed "
            "instance — non-deployed callers must never push.",
        )
        thread_cls.assert_not_called()

    # -- 1.172 ---------------------------------------------------------
    def test_1_172_deployed_first_call_schedules_push(self) -> None:
        """
        Scenario 1.172: in a deployed pod the first call schedules a daemon thread.
        Given: ``DEPLOYMENT_ENVIRONMENT`` + ``DOMAIN_BASE`` + ``LEX_API_KEY`` set
        When: ``report_lex_app_version_to_ic()`` is called for the first time
              in this process
        Then: it returns ``True`` and starts a daemon thread whose target is
              ``_do_report``. The daemon flag is mandatory — a non-daemon thread
              would block server shutdown waiting on IC.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(reporter.threading, "Thread") as thread_cls:
            thread_instance = MagicMock()
            thread_cls.return_value = thread_instance

            started = report_lex_app_version_to_ic()

        self.assertTrue(
            started,
            "report_lex_app_version_to_ic must return True when all three env "
            "vars are set and this is the first call in the process.",
        )
        thread_cls.assert_called_once()
        kwargs = thread_cls.call_args.kwargs
        self.assertIs(
            kwargs.get("target"),
            _do_report,
            "Background thread must target _do_report so the version is "
            "pushed over HTTP off the startup path.",
        )
        self.assertTrue(
            kwargs.get("daemon"),
            "Background thread must be a daemon — a non-daemon thread would "
            "block server shutdown until IC responds.",
        )
        thread_instance.start.assert_called_once()

    # -- 1.173 ---------------------------------------------------------
    def test_1_173_second_call_without_force_is_guarded(self) -> None:
        """
        Scenario 1.173: the once-per-process guard prevents repeated pushes.
        Given: a deployed env, and a prior successful call this process
        When: ``report_lex_app_version_to_ic()`` is called again
        Then: it returns ``False`` and starts no second thread — the reporter
              floods neither IC's endpoint nor the audit trail.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(reporter.threading, "Thread") as thread_cls:
            thread_cls.return_value = MagicMock()

            first = report_lex_app_version_to_ic()
            second = report_lex_app_version_to_ic()

        self.assertTrue(first, "First call should have succeeded for this scenario.")
        self.assertFalse(
            second,
            "Second call without force must be guarded — a True result here "
            "means the latch leaked and IC would receive a duplicate push on "
            "every Django ready() invocation in this process.",
        )
        self.assertEqual(
            thread_cls.call_count,
            1,
            "Exactly one Thread instance should have been constructed across "
            "both calls; the guard failed if more were created.",
        )

    # -- 1.174 ---------------------------------------------------------
    def test_1_174_force_bypasses_guard(self) -> None:
        """
        Scenario 1.174: ``force=True`` lets tests exercise the push path twice.
        Given: a deployed env and one prior call this process
        When: ``report_lex_app_version_to_ic(force=True)`` is called
        Then: it returns ``True`` and spawns a second thread — the force knob
              is the only documented escape from the once-per-process guard
              and exists explicitly so cluster tests like this one can run.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(reporter.threading, "Thread") as thread_cls:
            thread_cls.return_value = MagicMock()

            report_lex_app_version_to_ic()
            forced = report_lex_app_version_to_ic(force=True)

        self.assertTrue(
            forced,
            "force=True must bypass the once-per-process guard so tests can "
            "drive the push path; a False here makes the public force=True "
            "contract dead code.",
        )
        self.assertEqual(
            thread_cls.call_count,
            2,
            "Both the natural call and the forced call must construct their "
            "own Thread — force=True is the test-only escape from the latch.",
        )

    # -- 1.175 ---------------------------------------------------------
    def test_1_175_do_report_posts_correct_url_headers_and_body(self) -> None:
        """
        Scenario 1.175: ``_do_report`` shapes the IC HTTP call correctly.
        Given: a deployed env (DOMAIN_BASE, LEX_API_KEY) and an arbitrary version
        When: ``_do_report(version)`` is invoked
        Then: ``requests.post`` is called with
              URL  = ``https://{DOMAIN_BASE}/api/report_lex_app_version/``,
              header ``Authorization: Api-Key {LEX_API_KEY}``,
              JSON body ``{"version": <version>}``.
              The URL/header shape mirrors the contract IC's ``HasAPIKey``
              endpoints require (same as ``send_email`` and ``get_client_roles``).
              A drift here makes the push silently land as 401/404 and the UI
              keeps showing ``Unknown`` forever.
        """
        sentinel_version = "9.9.9-test"
        fake_response = MagicMock(status_code=200, text="ok")
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(reporter.requests, "post", return_value=fake_response) as post:
            _do_report(sentinel_version)

        post.assert_called_once()
        args, kwargs = post.call_args
        url = args[0] if args else kwargs.get("url")
        self.assertEqual(
            url,
            f"https://{_DEPLOYED_ENV['DOMAIN_BASE']}/api/report_lex_app_version/",
            "Wrong IC endpoint URL — must match the HasAPIKey route registered "
            "in instance_api/urls.py (report_lex_app_version/).",
        )
        self.assertEqual(
            kwargs.get("json"),
            {"version": sentinel_version},
            "Request body must be exactly {'version': <version>} — IC's "
            "ReportLexAppVersion view requires that key.",
        )
        headers = kwargs.get("headers") or {}
        self.assertEqual(
            headers.get("Authorization"),
            f"Api-Key {_DEPLOYED_ENV['LEX_API_KEY']}",
            "Authorization header must use IC's 'Api-Key <key>' scheme — the "
            "scheme HasAPIKey expects; anything else lands as 401.",
        )

    # -- 1.176 ---------------------------------------------------------
    def test_1_176_network_exception_is_swallowed(self) -> None:
        """
        Scenario 1.176: a ``requests.RequestException`` does not propagate.
        Given: a deployed env and a ``requests.post`` that raises
              ``requests.ConnectionError`` (IC unreachable / DNS / TLS fault)
        When: ``_do_report`` is invoked
        Then: it returns normally without raising — startup must survive a
              broken or paused IC, otherwise reporting a version becomes a
              hard dependency for the customer pod to come up at all.
        """
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(
                 reporter.requests,
                 "post",
                 side_effect=requests.ConnectionError("IC unreachable"),
             ):
            try:
                _do_report("1.2.3")
            except Exception as exc:  # noqa: BLE001 - the whole point is to assert no raise
                self.fail(
                    "_do_report must swallow network exceptions to keep "
                    f"startup non-blocking; it raised {type(exc).__name__}: {exc}"
                )

    # -- 1.177 ---------------------------------------------------------
    def test_1_177_non_200_response_is_swallowed(self) -> None:
        """
        Scenario 1.177: a non-200 IC response does not propagate.
        Given: a deployed env and ``requests.post`` returning HTTP 503
        When: ``_do_report`` is invoked
        Then: it returns normally — IC being temporarily down (or 401-ing on a
              rotated key) must not crash the customer pod's startup; the
              version will simply stay at its previous value in IC's DB.
        """
        failing_response = MagicMock(status_code=503, text="ic temporarily down")
        with patch.dict("os.environ", _DEPLOYED_ENV, clear=False), \
             patch.object(reporter.requests, "post", return_value=failing_response):
            try:
                _do_report("1.2.3")
            except Exception as exc:  # noqa: BLE001 - same rationale as 1.176
                self.fail(
                    "_do_report must swallow non-200 responses; it raised "
                    f"{type(exc).__name__}: {exc}"
                )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
