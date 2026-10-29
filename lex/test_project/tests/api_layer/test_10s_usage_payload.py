"""Cluster 10s: the usage payload -- api/user/'s "usage" key, the hash, the switch.

Intent
------
PostHog usage events must stay anonymous: the interface only ever gets a
per-instance pseudonym (``user_hash``), never a user id or email. lex-app
computes that hash and the rest of the usage payload server-side and hands it
to the interface through the existing ``api/user/`` response, under one new
``"usage"`` key -- no new endpoint, no config.js key. Four conditions must
each be able to turn the whole thing off: the ``LEX_USAGE_METRICS`` switch, a
missing per-instance ``LEX_USAGE_SALT``, an empty ``POSTHOG_PROJECT_KEY``, and
an unset ``INSTANCE_RESOURCE_IDENTIFIER`` (events no instance could be
credited with). In every one of those cases the payload must be exactly
``{"enabled": false}`` -- not a partial dict with some fields present --
because the interface decides whether to call ``posthog.init()`` purely from
``enabled``, and any key that leaked through unused (a key, a host) would be
dead weight at best and a quiet privacy mismatch at worst if the interface's
contract ever changed to trust it. And whatever happens while building it,
``api/user/`` itself must still answer: the interface cannot load without it.

A regression here reads as "an opted-out customer's PostHog project key is
still visible in their browser's network tab" (an information-exposure bug,
not a functional one) or "two instances' hashes for the same actual person
collide" (which would let fleet metrics join one person across instances --
exactly what the per-instance salt exists to prevent).

Cluster 10s -- scenarios 10.102-10.116. Type: U (10.113, 10.116 I/E -- drive the real
``api/user/`` view).
Covers: lex/core/services/usage_config.py, lex/authentication/views/user_api.py.
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10s_usage_payload.py -v
"""

from __future__ import annotations

import hashlib
import hmac
import os
from unittest.mock import MagicMock, patch

import pytest
from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from rest_framework.test import APIRequestFactory, force_authenticate

from lex.authentication.views.user_api import CurrentUser
from lex.core.services import usage_config

pytestmark = pytest.mark.api_layer

_ENABLED_ENV = {
    "LEX_USAGE_METRICS": "",
    "LEX_USAGE_SALT": "a" * 64,
    "POSTHOG_PROJECT_KEY": "phc_test_key",
    "POSTHOG_HOST": "",
    "INSTANCE_RESOURCE_IDENTIFIER": "example-prod-1",
}


class _User:
    """A minimal stand-in carrying only what user_hash()/usage_payload() read."""

    def __init__(self, username: str) -> None:
        self.username = username


class TestCluster10s_UsageMetricsSwitch(SimpleTestCase):
    """Cluster 10s: usage_metrics_enabled() -- the fail-closed on/off switch."""

    def setUp(self) -> None:
        super().setUp()
        usage_config._reset_for_tests()

    def tearDown(self) -> None:
        usage_config._reset_for_tests()
        super().tearDown()

    # -- 10.102 ----------------------------------------------------
    def test_10_102_unset_defaults_to_enabled(self) -> None:
        """
        Scenario 10.102: unset or empty means on.
        Given: LEX_USAGE_METRICS is not set at all
        When: usage_metrics_enabled() is asked
        Then: it is True -- usage metrics are on by default.
        """
        env = os.environ.copy()
        env.pop("LEX_USAGE_METRICS", None)
        with patch.dict("os.environ", env, clear=True):
            self.assertTrue(usage_config.usage_metrics_enabled())

    # -- 10.103 ----------------------------------------------------
    def test_10_103_recognised_true_like_values(self) -> None:
        """
        Scenario 10.103: true/1/yes/on, case- and whitespace-insensitive.
        Given: each of "true", "1", "yes", "on" in various cases and padding
        When: usage_metrics_enabled() is asked
        Then: it is True for all of them.
        """
        for raw in ("true", "TRUE", " True ", "1", "yes", "YES", "on", " On "):
            with patch.dict("os.environ", {"LEX_USAGE_METRICS": raw}, clear=False):
                self.assertTrue(
                    usage_config.usage_metrics_enabled(),
                    f"LEX_USAGE_METRICS={raw!r} must be recognised as on.",
                )

    # -- 10.104 ----------------------------------------------------
    def test_10_104_recognised_false_like_values(self) -> None:
        """
        Scenario 10.104: false/0/no/off, case- and whitespace-insensitive.
        Given: each of "false", "0", "no", "off" in various cases and padding
        When: usage_metrics_enabled() is asked
        Then: it is False for all of them.
        """
        for raw in ("false", "FALSE", " False ", "0", "no", "NO", "off", " Off "):
            with patch.dict("os.environ", {"LEX_USAGE_METRICS": raw}, clear=False):
                self.assertFalse(
                    usage_config.usage_metrics_enabled(),
                    f"LEX_USAGE_METRICS={raw!r} must be recognised as off.",
                )

    # -- 10.105 ----------------------------------------------------
    def test_10_105_unrecognised_value_fails_closed_and_warns_once(self) -> None:
        """
        Scenario 10.105: a typo turns recording OFF, with one warning per process.
        Given: LEX_USAGE_METRICS="enabled" (not a recognised spelling)
        When: usage_metrics_enabled() is called twice
        Then: both calls return False (fail closed -- a typo must never
              silently turn recording back on) and logger.warning fires once,
              not twice.
        """
        with patch.dict("os.environ", {"LEX_USAGE_METRICS": "enabled"}, clear=False), \
             patch.object(usage_config.logger, "warning") as warning:
            first = usage_config.usage_metrics_enabled()
            second = usage_config.usage_metrics_enabled()

        self.assertFalse(first)
        self.assertFalse(second)
        self.assertEqual(
            warning.call_count, 1,
            "An invalid value must warn at most once per process.",
        )


class TestCluster10s_UserHash(SimpleTestCase):
    """Cluster 10s: user_hash() -- the per-instance pseudonym."""

    # -- 10.106 ----------------------------------------------------
    def test_10_106_deterministic_for_the_same_user_and_salt(self) -> None:
        """
        Scenario 10.106: the same user and salt always hash the same way.
        Given: the same username and LEX_USAGE_SALT
        When: user_hash() is computed twice
        Then: both calls return the identical 32-character hex string -- the
              controller needs a stable key to count the same person across
              their session, not a fresh pseudonym every request.
        """
        user = _User("abcd1234hash@users.lex")
        with patch.dict("os.environ", {"LEX_USAGE_SALT": "salt-one"}, clear=False):
            first = usage_config.user_hash(user)
            second = usage_config.user_hash(user)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 32, "user_hash must be the first 32 hex characters.")
        int(first, 16)  # raises ValueError if not valid hex

    # -- 10.107 ----------------------------------------------------
    def test_10_107_a_different_salt_gives_a_different_hash(self) -> None:
        """
        Scenario 10.107: the per-instance salt is load-bearing for privacy.
        Given: the same username, two different LEX_USAGE_SALT values
        When: user_hash() is computed under each
        Then: the two hashes differ -- this is exactly the property that
              stops two instances from being joined on the same real person.
        """
        user = _User("abcd1234hash@users.lex")
        with patch.dict("os.environ", {"LEX_USAGE_SALT": "salt-one"}, clear=False):
            first = usage_config.user_hash(user)
        with patch.dict("os.environ", {"LEX_USAGE_SALT": "salt-two"}, clear=False):
            second = usage_config.user_hash(user)

        self.assertNotEqual(
            first, second,
            "Two instances with different salts must never hash the same "
            "person to the same value.",
        )

    # -- 10.114 ----------------------------------------------------
    def test_10_114_hash_matches_the_formula_computed_independently(self) -> None:
        """
        Scenario 10.114: user_hash() matches its formula exactly,
        not merely "is deterministic" or "changes with the salt" -- an
        implementation that swaps key and message, concatenates
        salt+username before hashing, or decodes the salt as hex
        (``bytes.fromhex``) instead of using its literal characters would
        pass 10.106 and 10.107 undetected, and any of those would silently
        re-key every pseudonym on the next deploy.
        Given: a fixed salt and a fixed username
        When: user_hash(user) is computed
        Then: it equals ``hmac.new(key, message, sha256).hexdigest()[:32]``
              computed independently in this test, with the salt's literal
              UTF-8 bytes as the key (LEX_USAGE_SALT is delivered "like
              LEX_API_KEY", an opaque string, never hex-decoded -- see
              usage_config.user_hash's docstring).
        """
        salt = "deadbeefcafe0123"
        username = "abcd1234hash@users.lex"
        user = _User(username)

        with patch.dict("os.environ", {"LEX_USAGE_SALT": salt}, clear=False):
            actual = usage_config.user_hash(user)

        expected = hmac.new(
            salt.encode("utf-8"), username.encode("utf-8"), hashlib.sha256,
        ).hexdigest()[:32]
        self.assertEqual(
            actual, expected,
            "user_hash() must match hmac_sha256(key=LEX_USAGE_SALT's UTF-8 "
            "bytes, message=username)[:32] exactly -- a formula drift here "
            "(swapped key/message, concatenation, or hex-decoding the salt) "
            "would silently re-key every pseudonym.",
        )


class TestCluster10s_UsagePayload(SimpleTestCase):
    """Cluster 10s: usage_payload() -- the dict api/user/ hands the interface."""

    def setUp(self) -> None:
        super().setUp()
        usage_config._reset_for_tests()
        self.user = _User("abcd1234hash@users.lex")

    def tearDown(self) -> None:
        usage_config._reset_for_tests()
        super().tearDown()

    # -- 10.108 ----------------------------------------------------
    def test_10_108_full_payload_when_everything_is_configured(self) -> None:
        """
        Scenario 10.108: the enabled shape, with the default PostHog host.
        Given: the switch on, a salt, a PostHog key, and no explicit POSTHOG_HOST
        When: usage_payload(user) is called
        Then: it returns enabled=True plus posthog_key, the default EU
              posthog_host, instance_rid and user_hash.
        """
        with patch.dict("os.environ", _ENABLED_ENV, clear=False):
            payload = usage_config.usage_payload(self.user)
            expected_hash = usage_config.user_hash(self.user)

        self.assertEqual(
            payload,
            {
                "enabled": True,
                "posthog_key": "phc_test_key",
                "posthog_host": usage_config.DEFAULT_POSTHOG_HOST,
                "instance_rid": "example-prod-1",
                "user_hash": expected_hash,
            },
        )

    # -- 10.109 ----------------------------------------------------
    def test_10_109_explicit_posthog_host_is_honoured(self) -> None:
        """
        Scenario 10.109: an explicit POSTHOG_HOST overrides the EU default.
        Given: the enabled env, plus POSTHOG_HOST set to a non-default value
        When: usage_payload(user) is called
        Then: posthog_host is exactly the configured value.
        """
        env = dict(_ENABLED_ENV, POSTHOG_HOST="https://self-hosted.example.com")
        with patch.dict("os.environ", env, clear=False):
            payload = usage_config.usage_payload(self.user)

        self.assertEqual(payload["posthog_host"], "https://self-hosted.example.com")

    # -- 10.110 ----------------------------------------------------
    def test_10_110_disabled_only_enabled_false_when_switch_is_off(self) -> None:
        """
        Scenario 10.110: the switch off collapses the payload to {"enabled": false}.
        Given: everything else configured, but LEX_USAGE_METRICS=off
        When: usage_payload(user) is called
        Then: the result is exactly {"enabled": False} -- no posthog_key,
              no user_hash leaks through even though the salt and key exist.
        """
        env = dict(_ENABLED_ENV, LEX_USAGE_METRICS="off")
        with patch.dict("os.environ", env, clear=False):
            payload = usage_config.usage_payload(self.user)

        self.assertEqual(payload, {"enabled": False})

    # -- 10.111 ----------------------------------------------------
    def test_10_111_disabled_only_enabled_false_when_salt_is_missing(self) -> None:
        """
        Scenario 10.111: a missing per-instance salt also collapses the payload.
        Given: the switch on and a PostHog key, but no LEX_USAGE_SALT
        When: usage_payload(user) is called
        Then: the result is exactly {"enabled": False}.
        """
        env = dict(_ENABLED_ENV, LEX_USAGE_SALT="")
        with patch.dict("os.environ", env, clear=False):
            payload = usage_config.usage_payload(self.user)

        self.assertEqual(payload, {"enabled": False})

    # -- 10.112 ----------------------------------------------------
    def test_10_112_disabled_only_enabled_false_when_posthog_key_is_empty(self) -> None:
        """
        Scenario 10.112: an empty POSTHOG_PROJECT_KEY also collapses the payload.
        Given: the switch on and a salt, but POSTHOG_PROJECT_KEY empty
               (empty means no PostHog)
        When: usage_payload(user) is called
        Then: the result is exactly {"enabled": False}.
        """
        env = dict(_ENABLED_ENV, POSTHOG_PROJECT_KEY="")
        with patch.dict("os.environ", env, clear=False):
            payload = usage_config.usage_payload(self.user)

        self.assertEqual(payload, {"enabled": False})

    # -- 10.115 ----------------------------------------------------
    def test_10_115_disabled_only_enabled_false_when_instance_rid_is_unset(self) -> None:
        """
        Scenario 10.115: an unset instance identifier also collapses the payload.
        Given: the switch on, a salt and a PostHog key, but
               INSTANCE_RESOURCE_IDENTIFIER empty or absent
        When: usage_payload(user) is called
        Then: the result is exactly {"enabled": False} -- events carrying a
              blank instance_rid could never be credited to any instance.
        """
        empty = dict(_ENABLED_ENV, INSTANCE_RESOURCE_IDENTIFIER="")
        absent = dict(os.environ, **_ENABLED_ENV)
        del absent["INSTANCE_RESOURCE_IDENTIFIER"]
        for label, env in (("empty", empty), ("absent", absent)):
            with self.subTest(instance_rid=label):
                with patch.dict("os.environ", env, clear=(label == "absent")):
                    payload = usage_config.usage_payload(self.user)
                self.assertEqual(
                    payload, {"enabled": False},
                    f"An {label} INSTANCE_RESOURCE_IDENTIFIER must turn usage off.",
                )


class TestCluster10s_CurrentUserEndpoint(TestCase):
    """Cluster 10s: api/user/ carries the usage payload end-to-end."""

    databases = {"default"}

    def setUp(self) -> None:
        super().setUp()
        usage_config._reset_for_tests()

    def tearDown(self) -> None:
        usage_config._reset_for_tests()
        super().tearDown()

    def _get(self):
        request = APIRequestFactory().get("/api/user/")
        request.session = {"oidc_access_token": "fake-oidc-token"}
        user = get_user_model().objects.create_user(username="abcd1234hash@users.lex")
        force_authenticate(request, user=user)

        userinfo = MagicMock(ok=True)
        userinfo.json.return_value = {"sub": "abcd1234hash@users.lex", "email": "redacted"}
        with patch("lex.authentication.views.user_api.requests.get", return_value=userinfo):
            return CurrentUser.as_view()(request), user

    # -- 10.113 ----------------------------------------------------
    def test_10_113_api_user_response_carries_the_usage_key(self) -> None:
        """
        Scenario 10.113: GET api/user/ hands the interface a "usage" key,
        beside the OIDC userinfo it already returns.
        Given: an authenticated request, a mocked OIDC userinfo call, and
               usage metrics fully configured
        When: CurrentUser's GET is driven directly (as the real view would be)
        Then: the response is 200, the original userinfo fields are
              unchanged, and "usage" matches usage_payload(request.user) for
              that same user -- the interface never has to call anything new.
        """
        with patch.dict("os.environ", _ENABLED_ENV, clear=False):
            response, user = self._get()
            expected_usage = usage_config.usage_payload(user)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["sub"], "abcd1234hash@users.lex")
        self.assertIn("usage", response.data)
        self.assertEqual(response.data["usage"], expected_usage)
        self.assertTrue(expected_usage["enabled"])

    # -- 10.116 ----------------------------------------------------
    def test_10_116_a_failing_usage_payload_never_blocks_sign_in(self) -> None:
        """
        Scenario 10.116: api/user/ still answers when building "usage" fails.
        Given: an authenticated request, a mocked OIDC userinfo call, and
               usage_payload raising
        When: CurrentUser's GET is driven
        Then: the response is 200 with the userinfo fields unchanged, "usage"
              is exactly {"enabled": False} (the interface starts without
              usage metrics), and the failure is logged at WARNING.
        """
        with patch.dict("os.environ", _ENABLED_ENV, clear=False), \
             patch(
                 "lex.authentication.views.user_api.usage_payload",
                 side_effect=RuntimeError("boom"),
             ), \
             self.assertLogs("lex.authentication.views.user_api", level="WARNING") as logs:
            response, _ = self._get()

        self.assertEqual(
            response.status_code, 200,
            "A failure building the usage payload must never fail api/user/.",
        )
        self.assertEqual(response.data["sub"], "abcd1234hash@users.lex")
        self.assertEqual(response.data["usage"], {"enabled": False})
        self.assertEqual(len(logs.records), 1, "The failure must be logged once.")
