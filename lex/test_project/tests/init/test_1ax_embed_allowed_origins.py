"""LEX_EMBED_ALLOWED_ORIGINS: named sites only may embed lex-app and use its filter bridge.

Intent: today any site can frame lex-app with ``embed=true`` --
``EmbedXFrameOptionsMiddleware`` strips ``X-Frame-Options`` and sets
``SameSite=None`` cookies for *any* caller that asks nicely. The branch this
test belongs to adds a `filter` bridge event that reports a row count with no
user action, so an unrestricted embed is no longer just a clickjacking risk --
it is a passive data leak to whatever page chooses to frame lex-app. Amendment
14 of the multi-value-filters design closes this: an operator names the sites
allowed to embed via ``LEX_EMBED_ALLOWED_ORIGINS``, and only those sites get a
browser-enforced ``Content-Security-Policy: frame-ancestors`` allowance and the
frontend flag that turns the filter channel on. Leaving the setting unset must
reproduce today's behaviour exactly -- this is an opt-in restriction layered on
an existing, already-shipped embed mechanism, not a new default.

A regression here either locks out every legitimate embed (setting parsed
wrong, or the CSP directive built wrong) or -- worse -- silently widens who can
frame lex-app and read its filter events (a dropped validation rule, a CSP
header that clobbers itself instead of merging, or a value that breaks out of
the ``config.js`` string literal it is spliced into).

Cluster 1ax -- scenarios 1.402-1.405. Type: U.
Covers: lex/runtime_config.py (parse_embed_allowed_origins), lex/lex_app/settings.py
(LEX_EMBED_ALLOWED_ORIGINS), lex/core/middleware/embed_xframe.py
(EmbedXFrameOptionsMiddleware.process_response), lex/react/views.py (serve_react's
config.js rewrite).
Run: python -m lex pytest lex/test_project/tests/init/test_1ax_embed_allowed_origins.py -v
"""

from __future__ import annotations

import importlib
import json
import os
from unittest import mock

import pytest
from django.http import HttpResponse
from django.test import RequestFactory, SimpleTestCase, override_settings

from lex.core.middleware.embed_xframe import EmbedXFrameOptionsMiddleware
from lex.lex_app import settings as lex_settings
from lex.react import views as react_views
from lex.runtime_config import parse_embed_allowed_origins

pytestmark = pytest.mark.init


_ALLOWED_ORIGINS = ["https://reports.example.com", "https://app.example.org:8443"]
_EXPECTED_FRAME_ANCESTORS = (
    "frame-ancestors 'self' https://reports.example.com https://app.example.org:8443"
)


def _run_middleware(request, response):
    """Call ``process_response`` directly -- no full middleware stack needed.

    ``EmbedXFrameOptionsMiddleware`` only implements ``process_response``, so a
    trivial ``get_response`` is enough to construct it; the response under test
    is passed straight in, exactly as it would arrive after
    ``XFrameOptionsMiddleware`` and ``SessionMiddleware`` have already run.
    """
    middleware = EmbedXFrameOptionsMiddleware(get_response=lambda r: response)
    return middleware.process_response(request, response)


def _serve(path: str, root) -> HttpResponse:
    """Call ``serve_react`` directly against a synthetic build directory."""
    request = RequestFactory().get(f"/{path}")
    return react_views.serve_react(request, path, document_root=str(root))


# ---------------------------------------------------------------------------
# 1.402 -- CSP frame-ancestors added on every exempt response, left off others
# ---------------------------------------------------------------------------
@override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS)
class TestCluster01ax_MiddlewareAddsFrameAncestors(SimpleTestCase):
    """Cluster 1ax: with origins configured, every exempt response is restricted
    to those origins via a CSP ``frame-ancestors`` directive."""

    def test_1_402_embed_true_response_gets_frame_ancestors(self):
        """
        Scenario 1.402: an ``embed=true`` response carries the configured directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request carrying
               ``?embed=true`` (the query-param exemption path)
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_402_oidc_path_response_gets_frame_ancestors(self):
        """
        Scenario 1.402: an OIDC-path response carries the configured directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request whose path
               starts with /oidc/ (the Keycloak round-trip exemption path)
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/oidc/callback")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_402_cookie_marked_response_gets_frame_ancestors(self):
        """
        Scenario 1.402: a response exempted by the ``_lex_embed`` cookie gets the directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request carrying the
               ``_lex_embed=1`` marker cookie from an earlier embed=true visit
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/some/view", HTTP_COOKIE="_lex_embed=1")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_402_non_embed_response_has_no_added_csp(self):
        """
        Scenario 1.402: an ordinary, non-exempt response is never given a CSP header.
        Given: LEX_EMBED_ALLOWED_ORIGINS configured, and a plain top-level request with
               no embed=true, no /oidc/ path, and no embed cookie
        When: the middleware processes the response
        Then: no Content-Security-Policy header is added -- only exempt responses
              carry the embed restriction
        """
        request = RequestFactory().get("/some/view")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertNotIn("Content-Security-Policy", response)

    def test_1_402_existing_csp_header_keeps_other_directives(self):
        """
        Scenario 1.402: merging the directive never drops what was already there.
        Given: an embed=true response that already carries a CSP header with
               unrelated directives (e.g. default-src, script-src)
        When: the middleware adds frame-ancestors
        Then: the original directives survive untouched, and frame-ancestors is
              appended alongside them
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = HttpResponse("ok")
        response["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'"

        response = _run_middleware(request, response)
        csp = response["Content-Security-Policy"]

        self.assertIn("default-src 'self'", csp, csp)
        self.assertIn("script-src 'self' 'unsafe-inline'", csp, csp)
        self.assertIn(_EXPECTED_FRAME_ANCESTORS, csp, csp)

    def test_1_402_existing_frame_ancestors_directive_is_replaced_not_duplicated(self):
        """
        Scenario 1.402: a stale frame-ancestors directive is replaced, not appended twice.
        Given: an embed=true response whose CSP already names a frame-ancestors
               directive (e.g. set upstream, pointing at a now-wrong origin)
        When: the middleware processes the response
        Then: the old frame-ancestors value is gone, the configured one appears
              exactly once, and the unrelated directive is preserved
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = HttpResponse("ok")
        response["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'self' https://old.example.com"
        )

        response = _run_middleware(request, response)
        csp = response["Content-Security-Policy"]

        self.assertIn("default-src 'self'", csp, csp)
        self.assertEqual(csp.count("frame-ancestors"), 1, csp)
        self.assertNotIn("old.example.com", csp, csp)
        self.assertIn(_EXPECTED_FRAME_ANCESTORS, csp, csp)


# ---------------------------------------------------------------------------
# 1.403 -- unset/empty setting: no CSP, pre-existing embed behaviour untouched
# ---------------------------------------------------------------------------
@override_settings(LEX_EMBED_ALLOWED_ORIGINS=[])
class TestCluster01ax_EmptyAllowListIsANoOp(SimpleTestCase):
    """Cluster 1ax: with no configured origins, behaviour is unchanged from
    before this setting existed -- no CSP, same X-Frame-Options/cookie handling."""

    def test_1_403_empty_allowed_origins_adds_no_csp_header(self):
        """
        Scenario 1.403: an empty allow-list never produces a CSP header.
        Given: LEX_EMBED_ALLOWED_ORIGINS == [] (what both an unset and a blank env
               var resolve to -- see TestCluster01ax_ParseEmbedAllowedOrigins for
               the env-parsing boundary itself)
        When: the middleware processes an embed=true response
        Then: no Content-Security-Policy header is added
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertNotIn("Content-Security-Policy", response)

    def test_1_403_xframe_options_still_stripped_and_cookies_still_patched(self):
        """
        Scenario 1.403: the pre-existing embed behaviour is untouched.
        Given: LEX_EMBED_ALLOWED_ORIGINS == [], and an embed=true response carrying
               X-Frame-Options plus session/CSRF cookies, as SessionMiddleware and
               CsrfViewMiddleware would have left them
        When: the middleware processes the response
        Then: X-Frame-Options is stripped and both cookies are patched to
              SameSite=None; Secure -- exactly as before this setting existed
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = HttpResponse("ok")
        response["X-Frame-Options"] = "DENY"
        response.set_cookie("sessionid", "abc123")
        response.set_cookie("csrftoken", "xyz789")

        response = _run_middleware(request, response)

        self.assertNotIn("X-Frame-Options", response)
        self.assertEqual(response.cookies["sessionid"]["samesite"], "None")
        self.assertTrue(response.cookies["sessionid"]["secure"])
        self.assertEqual(response.cookies["csrftoken"]["samesite"], "None")
        self.assertTrue(response.cookies["csrftoken"]["secure"])


# ---------------------------------------------------------------------------
# 1.404 -- config.js exposes the validated list to the frontend
# ---------------------------------------------------------------------------
@pytest.fixture
def config_js_build(tmp_path):
    """A build dir whose config.js carries the REACT_APP_EMBED_ALLOWED_ORIGINS
    placeholder among others -- the shape `public/config.js` ships (frontend task)."""
    (tmp_path / "config.js").write_text(
        "window.REACT_APP_KEYCLOAK_REALM = undefined\n"
        "window.REACT_APP_EMBED_ALLOWED_ORIGINS = undefined\n"
    )
    return tmp_path


@pytest.fixture
def config_js_build_without_placeholder(tmp_path):
    """A build dir whose config.js predates this feature: no placeholder line."""
    (tmp_path / "config.js").write_text("window.REACT_APP_KEYCLOAK_REALM = undefined\n")
    return tmp_path


class TestCluster01ax_ConfigJsEmbedOrigins:
    """Cluster 1ax: config.js exposes LEX_EMBED_ALLOWED_ORIGINS to the frontend
    as window.REACT_APP_EMBED_ALLOWED_ORIGINS."""

    def test_1_404_config_js_serves_comma_joined_origins(self, config_js_build):
        """
        Scenario 1.404: configured origins reach the frontend as a comma list.
        Given: the LEX_EMBED_ALLOWED_ORIGINS setting resolved to two origins
        When: config.js is served
        Then: window.REACT_APP_EMBED_ALLOWED_ORIGINS is the comma-joined list,
              as a properly quoted JS string
        """
        with mock.patch.object(react_views.settings, "LEX_EMBED_ALLOWED_ORIGINS", _ALLOWED_ORIGINS):
            response = _serve("config.js", config_js_build)

        body = response.content.decode()
        expected = (
            "window.REACT_APP_EMBED_ALLOWED_ORIGINS = "
            + json.dumps(",".join(_ALLOWED_ORIGINS))
        )
        assert expected in body, body

    def test_1_404_config_js_serves_empty_string_when_unset(self, config_js_build):
        """
        Scenario 1.404: an empty setting serves an empty string, not "undefined".
        Given: the LEX_EMBED_ALLOWED_ORIGINS setting resolved to []
        When: config.js is served
        Then: window.REACT_APP_EMBED_ALLOWED_ORIGINS = "" -- the frontend sees the
              documented "off" state, never a leftover "undefined"
        """
        with mock.patch.object(react_views.settings, "LEX_EMBED_ALLOWED_ORIGINS", []):
            response = _serve("config.js", config_js_build)

        body = response.content.decode()
        assert 'window.REACT_APP_EMBED_ALLOWED_ORIGINS = ""' in body, body
        assert "= undefined" not in body, body

    def test_1_404_placeholder_absent_is_left_untouched(self, config_js_build_without_placeholder):
        """
        Scenario 1.404: a config.js that predates this feature is served unchanged.
        Given: a config.js with no REACT_APP_EMBED_ALLOWED_ORIGINS placeholder line
               (an older frontend build)
        When: config.js is served, with origins configured
        Then: the response succeeds, carries no such line, and every other
              placeholder is still rewritten -- the new replacement is a no-op
              when its target text is absent
        """
        with mock.patch.object(react_views.settings, "LEX_EMBED_ALLOWED_ORIGINS", _ALLOWED_ORIGINS):
            response = _serve("config.js", config_js_build_without_placeholder)

        body = response.content.decode()
        assert "REACT_APP_EMBED_ALLOWED_ORIGINS" not in body, body
        assert "window.REACT_APP_KEYCLOAK_REALM = undefined" not in body, body

    def test_1_404_value_cannot_break_out_of_the_js_string_literal(self, tmp_path):
        """
        Scenario 1.404 (defence in depth): a hostile-looking value stays one JS string.
        Given: an origin list containing a value crafted to close a naively-quoted
               string literal early (validated origins can never actually contain
               a quote, but the splice must not assume that)
        When: config.js is served
        Then: the value is escaped (e.g. via json.dumps) so the quote is encoded,
              never breaking out of the assignment
        """
        (tmp_path / "config.js").write_text("window.REACT_APP_EMBED_ALLOWED_ORIGINS = undefined\n")
        hostile = 'https://evil.example.com";window.pwned=true;//'

        with mock.patch.object(react_views.settings, "LEX_EMBED_ALLOWED_ORIGINS", [hostile]):
            response = _serve("config.js", tmp_path)

        body = response.content.decode()
        expected = "window.REACT_APP_EMBED_ALLOWED_ORIGINS = " + json.dumps(hostile)
        assert expected in body, body
        assert '= "https://evil.example.com";window.pwned=true;//"' not in body, body


# ---------------------------------------------------------------------------
# 1.405 -- parsing/validation: normalisation, and invalid entries dropped+logged
# ---------------------------------------------------------------------------
class TestCluster01ax_ParseEmbedAllowedOrigins(SimpleTestCase):
    """Cluster 1ax: parse_embed_allowed_origins -- the pure validator settings.py calls."""

    def test_1_405_unset_or_blank_gives_empty_list(self):
        """
        Scenario 1.405: no configured origins is the documented "off" state, not an error.
        Given: raw is None, "", or whitespace-only
        When: parse_embed_allowed_origins parses it
        Then: it returns [] every time, with nothing logged
        """
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                with self.assertNoLogs("lex.runtime_config", level="WARNING"):
                    self.assertEqual(parse_embed_allowed_origins(raw), [])

    def test_1_405_valid_origins_are_parsed_and_normalised(self):
        """
        Scenario 1.405: well-formed origins pass through, case-normalised.
        Given: two valid origins -- one with a port, one with a mixed-case scheme and host
        When: parse_embed_allowed_origins parses the comma-separated raw value
        Then: both come back lower-cased as scheme://host[:port], in their original
              order, with nothing logged
        """
        raw = "https://reports.example.com,HTTPS://APP.Example.ORG:8443"
        with self.assertNoLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, _ALLOWED_ORIGINS)

    def test_1_405_duplicate_origins_collapse_to_one(self):
        """
        Scenario 1.405 (normalisation): the same origin twice is listed once.
        Given: the same origin repeated, differing only by scheme case
        When: parse_embed_allowed_origins parses it
        Then: it appears once, at its first position
        """
        result = parse_embed_allowed_origins("https://reports.example.com,HTTPS://reports.example.com")

        self.assertEqual(result, ["https://reports.example.com"])

    def test_1_405_blank_segments_from_stray_commas_are_skipped_quietly(self):
        """
        Scenario 1.405: a stray/trailing comma is a formatting artifact, not a bad origin.
        Given: "https://a.example.com,,https://b.example.com," (double and trailing comma)
        When: parse_embed_allowed_origins parses it
        Then: both real origins come back and nothing is logged for the empty segments
        """
        with self.assertNoLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins("https://a.example.com,,https://b.example.com,")

        self.assertEqual(result, ["https://a.example.com", "https://b.example.com"])

    def test_1_405_invalid_entries_are_dropped_with_one_warning_naming_them(self):
        """
        Scenario 1.405: a path, a wildcard, a non-http scheme and garbage are all dropped.
        Given: one valid origin alongside a path, a wildcard, an ftp scheme, and garbage
        When: parse_embed_allowed_origins parses the mixed raw value
        Then: only the valid origin survives, and exactly one WARNING log names all
              four invalid entries together -- a typo must not crash startup, but
              must not pass silently either
        """
        raw = (
            "https://reports.example.com,"
            "https://bad.example.com/dashboard,"
            "https://*.example.com,"
            "ftp://files.example.com,"
            "not a valid origin"
        )

        with self.assertLogs("lex.runtime_config", level="WARNING") as captured:
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, ["https://reports.example.com"])
        self.assertEqual(len(captured.records), 1, captured.output)
        message = captured.records[0].getMessage()
        self.assertIn("https://bad.example.com/dashboard", message)
        self.assertIn("https://*.example.com", message)
        self.assertIn("ftp://files.example.com", message)
        self.assertIn("not a valid origin", message)

    def test_1_405_credentials_query_and_fragment_are_also_invalid(self):
        """
        Scenario 1.405 (boundary): userinfo, a query string and a fragment are all rejected.
        Given: an origin carrying embedded credentials, one carrying a query string,
               and one carrying a fragment -- none of them bare scheme://host[:port]
        When: parse_embed_allowed_origins parses each alongside one valid origin
        Then: all three are dropped and the valid origin alone survives
        """
        raw = (
            "https://reports.example.com,"
            "https://user:pass@sneaky.example.com,"
            "https://query.example.com?x=1,"
            "https://frag.example.com#top"
        )

        with self.assertLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, ["https://reports.example.com"])


class TestCluster01ax_SettingReadsEnvVar(SimpleTestCase):
    """Cluster 1ax (scenario 1.405): lex.lex_app.settings wires
    os.getenv("LEX_EMBED_ALLOWED_ORIGINS") through parse_embed_allowed_origins --
    this is the glue, not just the helper function in isolation.

    Reloading re-executes the settings module top-to-bottom, the same technique
    the pre-existing 1s cluster uses for its own env-driven settings; the saved
    env var is restored and the module reloaded again in cleanup either way.
    """

    def setUp(self):
        self._saved_env = os.environ.get("LEX_EMBED_ALLOWED_ORIGINS")
        self.addCleanup(self._restore_env_and_reload)

    def _restore_env_and_reload(self):
        if self._saved_env is None:
            os.environ.pop("LEX_EMBED_ALLOWED_ORIGINS", None)
        else:
            os.environ["LEX_EMBED_ALLOWED_ORIGINS"] = self._saved_env
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)

    def _reload(self, value):
        if value is None:
            os.environ.pop("LEX_EMBED_ALLOWED_ORIGINS", None)
        else:
            os.environ["LEX_EMBED_ALLOWED_ORIGINS"] = value
        with mock.patch("sentry_sdk.init"):
            importlib.reload(lex_settings)
        return lex_settings

    def test_1_405_setting_is_the_parsed_env_var(self):
        """
        Scenario 1.405: the Django setting is parse_embed_allowed_origins(os.getenv(...)),
        not a hand-rolled re-implementation.
        Given: LEX_EMBED_ALLOWED_ORIGINS set in the environment to one valid and
               one invalid origin
        When: lex.lex_app.settings is (re)imported
        Then: the module-level LEX_EMBED_ALLOWED_ORIGINS equals exactly what the
              parser returns for that raw value
        """
        settings_mod = self._reload("https://reports.example.com,not-a-url")

        self.assertEqual(settings_mod.LEX_EMBED_ALLOWED_ORIGINS, ["https://reports.example.com"])

    def test_1_405_setting_is_empty_list_when_env_var_unset(self):
        """
        Scenario 1.405: the documented "off" state holds at the settings layer too.
        Given: LEX_EMBED_ALLOWED_ORIGINS is not present in the environment
        When: lex.lex_app.settings is (re)imported
        Then: the setting is [] -- never None, never a string
        """
        settings_mod = self._reload(None)

        self.assertEqual(settings_mod.LEX_EMBED_ALLOWED_ORIGINS, [])
