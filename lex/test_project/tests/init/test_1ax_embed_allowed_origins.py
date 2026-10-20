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

``EmbedXFrameOptionsMiddleware`` is not registered in this repo's ``MIDDLEWARE``
today (see batches.md), so it is not what actually makes lex-app frameable --
``serve_react`` is: it is decorated ``@xframe_options_exempt``, so Django's own
``XFrameOptionsMiddleware`` never protects its HTML documents. The 1.410 scenario
pins the ``frame-ancestors`` restriction where it actually takes effect: on
``serve_react``'s own document responses (the SPA fallback and any other real
``.html`` file it serves), never on the assets or ``config.js`` beside them.

A security review of the first cut found that ``urlsplit`` alone is not enough to
turn a host into something safe to splice into a CSP header: a non-ASCII host
produces non-ASCII header bytes (silently dropping the whole directive), and a
crafted bracketed/whitespace host can inject an extra CSP source token (e.g. a
bare ``https:`` scheme source), widening -- not narrowing -- who may frame
lex-app. ``_normalise_origin`` additionally IDNA-encodes the host and checks it
against a strict hostname grammar; neither step is optional. The review also
found that ``LEX_EMBED_ALLOWED_ORIGINS`` set to only invalid entries (e.g. a
wildcard) parsed to ``[]``, indistinguishable from unset -- silently disabling
the restriction the operator thought they had turned on. ``LEX_EMBED_ORIGINS_CONFIGURED``
closes that: set-but-empty now fails closed to ``frame-ancestors 'self'`` (the
app may still frame itself; nothing else may), on both the middleware and the
SPA document.

A re-review found one further host-validation gap: Python's stdlib IDNA2003
codec and a browser's WHATWG/UTS46 (non-transitional) domain-to-ASCII disagree
on four code points (sharp s, final sigma, ZWJ, ZWNJ) -- each one folds or
drops here in a way a browser does not, so the two sides compute different
Punycode for the same typed host. ``_normalise_origin`` now rejects a host
containing any of them outright, before encoding, rather than accepting
whichever side's answer Python happens to produce. The same review also found
the hostname grammar's ``.match()`` + ``$`` would tolerate a trailing newline
(``$`` matches just before one); it now uses ``.fullmatch()``. The scenario IDs
(1.406-1.410) are unchanged across every round -- each one only adds cases to
the existing numbers.

Cluster 1ax -- scenarios 1.406-1.410. Type: U.
Covers: lex/runtime_config.py (parse_embed_allowed_origins, merge_frame_ancestors),
lex/lex_app/settings.py (LEX_EMBED_ALLOWED_ORIGINS), lex/core/middleware/embed_xframe.py
(EmbedXFrameOptionsMiddleware.process_response), lex/react/views.py (serve_react's
config.js rewrite and its own document responses' Content-Security-Policy).
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
from lex.runtime_config import embed_origins_configured, parse_embed_allowed_origins

pytestmark = pytest.mark.init


_ALLOWED_ORIGINS = ["https://reports.example.com", "https://app.example.org:8443"]
_EXPECTED_FRAME_ANCESTORS = (
    "frame-ancestors 'self' https://reports.example.com https://app.example.org:8443"
)
_SELF_ONLY_DIRECTIVE = "frame-ancestors 'self'"


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
# 1.406 -- CSP frame-ancestors added on every exempt response, left off others
# ---------------------------------------------------------------------------
@override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS)
class TestCluster01ax_MiddlewareAddsFrameAncestors(SimpleTestCase):
    """Cluster 1ax: with origins configured, every exempt response is restricted
    to those origins via a CSP ``frame-ancestors`` directive."""

    def test_1_406_embed_true_response_gets_frame_ancestors(self):
        """
        Scenario 1.406: an ``embed=true`` response carries the configured directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request carrying
               ``?embed=true`` (the query-param exemption path)
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_406_oidc_path_response_gets_frame_ancestors(self):
        """
        Scenario 1.406: an OIDC-path response carries the configured directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request whose path
               starts with /oidc/ (the Keycloak round-trip exemption path)
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/oidc/callback")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_406_cookie_marked_response_gets_frame_ancestors(self):
        """
        Scenario 1.406: a response exempted by the ``_lex_embed`` cookie gets the directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS names two origins, and a request carrying the
               ``_lex_embed=1`` marker cookie from an earlier embed=true visit
        When: the middleware processes the response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' <both origins>
        """
        request = RequestFactory().get("/some/view", HTTP_COOKIE="_lex_embed=1")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _EXPECTED_FRAME_ANCESTORS)

    def test_1_406_non_embed_response_has_no_added_csp(self):
        """
        Scenario 1.406: an ordinary, non-exempt response is never given a CSP header.
        Given: LEX_EMBED_ALLOWED_ORIGINS configured, and a plain top-level request with
               no embed=true, no /oidc/ path, and no embed cookie
        When: the middleware processes the response
        Then: no Content-Security-Policy header is added -- only exempt responses
              carry the embed restriction
        """
        request = RequestFactory().get("/some/view")
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertNotIn("Content-Security-Policy", response)

    def test_1_406_existing_csp_header_keeps_other_directives(self):
        """
        Scenario 1.406: merging the directive never drops what was already there.
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

    def test_1_406_existing_frame_ancestors_directive_is_replaced_not_duplicated(self):
        """
        Scenario 1.406: a stale frame-ancestors directive is replaced, not appended twice.
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

    @override_settings(LEX_EMBED_ALLOWED_ORIGINS=[], LEX_EMBED_ORIGINS_CONFIGURED=True)
    def test_1_406_configured_but_all_invalid_still_gets_self_only_directive(self):
        """
        Scenario 1.406 (fail closed): set-but-nothing-valid restricts to 'self', not open.
        Given: an operator set LEX_EMBED_ALLOWED_ORIGINS to something (e.g. a wildcard
               that normalised to nothing), so LEX_EMBED_ORIGINS_CONFIGURED is True even
               though the parsed list is empty
        When: the middleware processes an embed=true response
        Then: Content-Security-Policy is exactly frame-ancestors 'self' -- the app may
              still frame itself, but nothing else may. Silently behaving as if the
              setting were unset (no CSP at all) would reopen unrestricted framing the
              moment every configured origin happens to be invalid.
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertEqual(response["Content-Security-Policy"], _SELF_ONLY_DIRECTIVE)


# ---------------------------------------------------------------------------
# 1.407 -- unset/empty setting: no CSP, pre-existing embed behaviour untouched
# ---------------------------------------------------------------------------
@override_settings(LEX_EMBED_ALLOWED_ORIGINS=[], LEX_EMBED_ORIGINS_CONFIGURED=False)
class TestCluster01ax_EmptyAllowListIsANoOp(SimpleTestCase):
    """Cluster 1ax: with no configured origins, behaviour is unchanged from
    before this setting existed -- no CSP, same X-Frame-Options/cookie handling.

    ``LEX_EMBED_ORIGINS_CONFIGURED`` is pinned False explicitly (not just left to
    whatever the test environment's real env var happens to be) -- this class is
    specifically the *unset* case; TestCluster01ax_MiddlewareAddsFrameAncestors
    covers set-but-empty, which must NOT behave like this one.
    """

    def test_1_407_empty_allowed_origins_adds_no_csp_header(self):
        """
        Scenario 1.407: an empty allow-list never produces a CSP header.
        Given: LEX_EMBED_ALLOWED_ORIGINS == [] and LEX_EMBED_ORIGINS_CONFIGURED == False
               (what an unset or a blank env var resolve to -- see
               TestCluster01ax_ParseEmbedAllowedOrigins for the env-parsing boundary
               itself, and TestCluster01ax_MiddlewareAddsFrameAncestors for the
               set-but-empty fail-closed case this is NOT)
        When: the middleware processes an embed=true response
        Then: no Content-Security-Policy header is added
        """
        request = RequestFactory().get("/some/view", {"embed": "true"})
        response = _run_middleware(request, HttpResponse("ok"))

        self.assertNotIn("Content-Security-Policy", response)

    def test_1_407_xframe_options_still_stripped_and_cookies_still_patched(self):
        """
        Scenario 1.407: the pre-existing embed behaviour is untouched.
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
# 1.408 -- config.js exposes the validated list to the frontend
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

    def test_1_408_config_js_serves_comma_joined_origins(self, config_js_build):
        """
        Scenario 1.408: configured origins reach the frontend as a comma list.
        Given: the LEX_EMBED_ALLOWED_ORIGINS setting resolved to two origins
        When: config.js is served
        Then: window.REACT_APP_EMBED_ALLOWED_ORIGINS is the comma-joined list,
              as a properly quoted JS string
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("config.js", config_js_build)

        body = response.content.decode()
        expected = (
            "window.REACT_APP_EMBED_ALLOWED_ORIGINS = "
            + json.dumps(",".join(_ALLOWED_ORIGINS))
        )
        assert expected in body, body

    def test_1_408_config_js_serves_empty_string_when_unset(self, config_js_build):
        """
        Scenario 1.408: an empty setting serves an empty string, not "undefined".
        Given: the LEX_EMBED_ALLOWED_ORIGINS setting resolved to []
        When: config.js is served
        Then: window.REACT_APP_EMBED_ALLOWED_ORIGINS = "" -- the frontend sees the
              documented "off" state, never a leftover "undefined"
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=[], LEX_EMBED_ORIGINS_CONFIGURED=False):
            response = _serve("config.js", config_js_build)

        body = response.content.decode()
        assert 'window.REACT_APP_EMBED_ALLOWED_ORIGINS = ""' in body, body
        assert "= undefined" not in body, body

    def test_1_408_placeholder_absent_is_left_untouched(self, config_js_build_without_placeholder):
        """
        Scenario 1.408: a config.js that predates this feature is served unchanged.
        Given: a config.js with no REACT_APP_EMBED_ALLOWED_ORIGINS placeholder line
               (an older frontend build)
        When: config.js is served, with origins configured
        Then: the response succeeds, carries no such line, and every other
              placeholder is still rewritten -- the new replacement is a no-op
              when its target text is absent
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("config.js", config_js_build_without_placeholder)

        body = response.content.decode()
        assert "REACT_APP_EMBED_ALLOWED_ORIGINS" not in body, body
        assert "window.REACT_APP_KEYCLOAK_REALM = undefined" not in body, body

    def test_1_408_value_cannot_break_out_of_the_js_string_literal(self, tmp_path):
        """
        Scenario 1.408 (defence in depth): a hostile-looking value stays one JS string.
        Given: an origin list containing a value crafted to close a naively-quoted
               string literal early (validated origins can never actually contain
               a quote, but the splice must not assume that)
        When: config.js is served
        Then: the value is escaped (e.g. via json.dumps) so the quote is encoded,
              never breaking out of the assignment
        """
        (tmp_path / "config.js").write_text("window.REACT_APP_EMBED_ALLOWED_ORIGINS = undefined\n")
        hostile = 'https://evil.example.com";window.pwned=true;//'

        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=[hostile]):
            response = _serve("config.js", tmp_path)

        body = response.content.decode()
        expected = "window.REACT_APP_EMBED_ALLOWED_ORIGINS = " + json.dumps(hostile)
        assert expected in body, body
        assert '= "https://evil.example.com";window.pwned=true;//"' not in body, body


# ---------------------------------------------------------------------------
# 1.409 -- parsing/validation: normalisation, and invalid entries dropped+logged
# ---------------------------------------------------------------------------
class TestCluster01ax_ParseEmbedAllowedOrigins(SimpleTestCase):
    """Cluster 1ax: parse_embed_allowed_origins -- the pure validator settings.py calls."""

    def test_1_409_unset_or_blank_gives_empty_list(self):
        """
        Scenario 1.409: no configured origins is the documented "off" state, not an error.
        Given: raw is None, "", or whitespace-only
        When: parse_embed_allowed_origins parses it
        Then: it returns [] every time, with nothing logged
        """
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                with self.assertNoLogs("lex.runtime_config", level="WARNING"):
                    self.assertEqual(parse_embed_allowed_origins(raw), [])

    def test_1_409_valid_origins_are_parsed_and_normalised(self):
        """
        Scenario 1.409: well-formed origins pass through, case-normalised.
        Given: two valid origins -- one with a port, one with a mixed-case scheme and host
        When: parse_embed_allowed_origins parses the comma-separated raw value
        Then: both come back lower-cased as scheme://host[:port], in their original
              order, with nothing logged
        """
        raw = "https://reports.example.com,HTTPS://APP.Example.ORG:8443"
        with self.assertNoLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, _ALLOWED_ORIGINS)

    def test_1_409_duplicate_origins_collapse_to_one(self):
        """
        Scenario 1.409 (normalisation): the same origin twice is listed once.
        Given: the same origin repeated, differing only by scheme case
        When: parse_embed_allowed_origins parses it
        Then: it appears once, at its first position
        """
        result = parse_embed_allowed_origins("https://reports.example.com,HTTPS://reports.example.com")

        self.assertEqual(result, ["https://reports.example.com"])

    def test_1_409_blank_segments_from_stray_commas_are_skipped_quietly(self):
        """
        Scenario 1.409: a stray/trailing comma is a formatting artifact, not a bad origin.
        Given: "https://a.example.com,,https://b.example.com," (double and trailing comma)
        When: parse_embed_allowed_origins parses it
        Then: both real origins come back and nothing is logged for the empty segments
        """
        with self.assertNoLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins("https://a.example.com,,https://b.example.com,")

        self.assertEqual(result, ["https://a.example.com", "https://b.example.com"])

    def test_1_409_invalid_entries_are_dropped_with_one_warning_naming_them(self):
        """
        Scenario 1.409: a path, a wildcard, a non-http scheme and garbage are all dropped.
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

    def test_1_409_credentials_query_and_fragment_are_also_invalid(self):
        """
        Scenario 1.409 (boundary): userinfo, a query string and a fragment are all rejected.
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

    def test_1_409_idn_hosts_are_accepted_as_punycode(self):
        """
        Scenario 1.409 (security fix): a non-ASCII host is IDNA-encoded, not rejected
        or passed through raw.
        Given: an internationalised domain, mixed with an ASCII one
        When: parse_embed_allowed_origins parses them
        Then: both come back as their ASCII punycode form -- a raw Unicode host would
              either break the HTTP header (non-ASCII bytes) or silently lose the
              whole directive; it must never be spliced in as-is
        """
        raw = "https://müller.de,https://reports.example.com"

        with self.assertNoLogs("lex.runtime_config", level="WARNING"):
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(
            result, ["https://xn--mller-kva.de", "https://reports.example.com"]
        )

    def test_1_409_idna_divergent_hosts_are_rejected(self):
        """
        Scenario 1.409 (security fix, re-review): a host where Python's stdlib
        IDNA2003 and a browser's WHATWG/UTS46 (non-transitional) domain-to-ASCII
        disagree is dropped, not silently encoded to the wrong domain.
        Given: "fußball.de" -- IDNA2003 case-folds ß (U+00DF) to "ss", giving the
               plain-ASCII "fussball.de", while a browser keeps ß and
               Punycode-encodes the host to "xn--fuball-cta.de", an entirely
               different domain -- and "ελλάς.gr", whose trailing final sigma
               (ς, U+03C2) folds to regular sigma (σ) here but not there,
               producing different Punycode either way
        When: parse_embed_allowed_origins parses each, alongside one valid origin
        Then: both are dropped and named in the warning. Accepting either would
              put a domain in the CSP header that does not match what the
              operator's actual site resolves to in a browser -- the operator's
              real site would still be refused framing (or, worse, some other,
              unintended domain that happens to match the mis-encoded form would
              be granted it)
        """
        raw = "https://reports.example.com,https://fußball.de,https://ελλάς.gr"

        with self.assertLogs("lex.runtime_config", level="WARNING") as captured:
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, ["https://reports.example.com"])
        message = captured.records[0].getMessage()
        self.assertIn("https://fußball.de", message, message)
        self.assertIn("https://ελλάς.gr", message, message)

    def test_1_409_bracketed_and_syntax_breaking_hosts_are_rejected(self):
        """
        Scenario 1.409 (security fix): a host is not just "whatever urlsplit accepted".
        Given: an IPv6-literal-shaped bracketed host, and a bracketed host crafted to
               inject a bare `https:` CSP source token through the space inside it,
               alongside hosts carrying `;`, a space, a quote, a backslash and `%`
        When: parse_embed_allowed_origins parses each, alongside one valid origin
        Then: every one of them is dropped -- none may reach the CSP header, where
              a `;`/quote/bare-scheme could inject a new directive or source, and a
              non-hostname character could break the header some other way
        """
        raw = (
            "https://reports.example.com,"
            "https://[::1],"
            "https://[v1.x https:],"
            "https://a.com;script-src,"
            "https://evil .com,"
            'https://evil.com",'
            "https://evil.com\\,"
            "https://evil.com%0d%0a"
        )

        with self.assertLogs("lex.runtime_config", level="WARNING") as captured:
            result = parse_embed_allowed_origins(raw)

        self.assertEqual(result, ["https://reports.example.com"])
        message = captured.records[0].getMessage()
        for bad in (
            "https://[::1]",
            "https://[v1.x https:]",
            "https://a.com;script-src",
            "https://evil .com",
            'https://evil.com"',
            "https://evil.com\\",
            "https://evil.com%0d%0a",
        ):
            self.assertIn(bad, message, message)

    def test_1_409_default_ports_are_dropped(self):
        """
        Scenario 1.409 (browser-origin form): :443 on https and :80 on http vanish.
        Given: an https origin on :443 and an http origin on :80 -- each one's own
               scheme's default port
        When: parse_embed_allowed_origins parses them
        Then: both come back without a port, matching the form a browser's own
              MessageEvent.origin would take -- a non-default port (already covered
              by test_1_409_valid_origins_are_parsed_and_normalised's :8443 case) is
              kept
        """
        result = parse_embed_allowed_origins("https://x.example.com:443,http://y.example.com:80")

        self.assertEqual(result, ["https://x.example.com", "http://y.example.com"])

    def test_1_409_a_lone_trailing_slash_is_accepted(self):
        """
        Scenario 1.409 (browser-origin form): a bare trailing slash is not a path.
        Given: "https://x.example.com/" -- a trailing slash and nothing else
        When: parse_embed_allowed_origins parses it
        Then: it is accepted as "https://x.example.com" -- the origin of a URL with
              an empty path is the same origin regardless of the trailing slash. Any
              further path segment (covered by the existing .../dashboard case) is
              still invalid.
        """
        result = parse_embed_allowed_origins("https://x.example.com/")

        self.assertEqual(result, ["https://x.example.com"])

    def test_1_409_duplicates_after_port_normalisation_collapse_to_one(self):
        """
        Scenario 1.409 (normalisation): dropping the default port can create a
        duplicate that didn't look like one in the raw value.
        Given: the same host once with an explicit default port and once without
        When: parse_embed_allowed_origins parses them
        Then: only one entry survives, at its first position -- normalising ports
              before de-duplicating is what makes this collapse at all
        """
        result = parse_embed_allowed_origins("https://x.example.com:443,https://x.example.com")

        self.assertEqual(result, ["https://x.example.com"])

    def test_1_409_configured_but_all_invalid_fails_closed_with_a_warning(self):
        """
        Scenario 1.409 (fail closed): the parser itself flags "configured but empty".
        Given: LEX_EMBED_ALLOWED_ORIGINS set to only a wildcard -- syntactically
               "something", but nothing that survives validation
        When: parse_embed_allowed_origins parses it
        Then: it returns [] (as it must -- the wildcard is still invalid) and logs a
              second WARNING, distinct from the "dropping invalid origin(s)" one,
              saying framing is limited to the app itself -- this is the signal
              embed_xframe.py and serve_react use (via LEX_EMBED_ORIGINS_CONFIGURED)
              to fail closed instead of silently behaving as if nothing were set
        """
        with self.assertLogs("lex.runtime_config", level="WARNING") as captured:
            result = parse_embed_allowed_origins("https://*.example.com")

        self.assertEqual(result, [])
        messages = [r.getMessage() for r in captured.records]
        self.assertTrue(
            any("limited to the app itself" in m for m in messages), messages
        )

    def test_1_409_embed_origins_configured_distinguishes_blank_from_set(self):
        """
        Scenario 1.409: embed_origins_configured answers "was anything written?",
        not "did anything validate?".
        Given: None, "", whitespace-only, a valid origin, and a value that is
               syntactically present but will fail every validation rule
        When: embed_origins_configured inspects each raw value
        Then: only the blank/unset forms are False -- a value that is present but
              invalid is still True, which is exactly the case that must fail closed
              rather than being treated as unset
        """
        for raw in (None, "", "   "):
            with self.subTest(raw=raw):
                self.assertFalse(embed_origins_configured(raw))

        for raw in ("https://reports.example.com", "https://*.example.com", "garbage"):
            with self.subTest(raw=raw):
                self.assertTrue(embed_origins_configured(raw))


class TestCluster01ax_SettingReadsEnvVar(SimpleTestCase):
    """Cluster 1ax (scenario 1.409): lex.lex_app.settings wires
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

    def test_1_409_setting_is_the_parsed_env_var(self):
        """
        Scenario 1.409: the Django setting is parse_embed_allowed_origins(os.getenv(...)),
        not a hand-rolled re-implementation.
        Given: LEX_EMBED_ALLOWED_ORIGINS set in the environment to one valid and
               one invalid origin
        When: lex.lex_app.settings is (re)imported
        Then: the module-level LEX_EMBED_ALLOWED_ORIGINS equals exactly what the
              parser returns for that raw value
        """
        settings_mod = self._reload("https://reports.example.com,not-a-url")

        self.assertEqual(settings_mod.LEX_EMBED_ALLOWED_ORIGINS, ["https://reports.example.com"])

    def test_1_409_setting_is_empty_list_when_env_var_unset(self):
        """
        Scenario 1.409: the documented "off" state holds at the settings layer too.
        Given: LEX_EMBED_ALLOWED_ORIGINS is not present in the environment
        When: lex.lex_app.settings is (re)imported
        Then: the setting is [] -- never None, never a string
        """
        settings_mod = self._reload(None)

        self.assertEqual(settings_mod.LEX_EMBED_ALLOWED_ORIGINS, [])

    def test_1_409_origins_configured_setting_reflects_env_var(self):
        """
        Scenario 1.409 (fail closed): LEX_EMBED_ORIGINS_CONFIGURED is wired the
        same way LEX_EMBED_ALLOWED_ORIGINS is -- from the real env var, not
        hand-maintained separately.
        Given: the env var set to a wildcard-only value (parses to [], but was
               written), and then unset
        When: lex.lex_app.settings is (re)imported each time
        Then: LEX_EMBED_ORIGINS_CONFIGURED is True for the set-but-invalid case and
              False when unset -- the exact distinction the middleware and
              serve_react rely on to fail closed instead of wide open
        """
        configured = self._reload("https://*.example.com")
        self.assertEqual(configured.LEX_EMBED_ALLOWED_ORIGINS, [])
        self.assertIs(configured.LEX_EMBED_ORIGINS_CONFIGURED, True)

        unset = self._reload(None)
        self.assertIs(unset.LEX_EMBED_ORIGINS_CONFIGURED, False)


# ---------------------------------------------------------------------------
# 1.410 -- the SPA document itself carries frame-ancestors
#
# serve_react is @xframe_options_exempt, so Django's own XFrameOptionsMiddleware
# never protects it -- the SPA's HTML documents (index.html, the SPA fallback for
# any client-routed path, and any other real .html entry point such as embed.html)
# are the one place nothing else restricts who may frame lex-app while
# EmbedXFrameOptionsMiddleware is unregistered, as it is today (see batches.md).
# Hashed assets and config.js are not documents and must never get the directive.
# ---------------------------------------------------------------------------
@pytest.fixture
def spa_build(tmp_path):
    """A minimal SPA build: the shell, a second real HTML entry point (embed.html,
    as `lex/react/views.py` itself documents it -- a second Vite entry, not a
    route), a hashed asset, and config.js."""
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>lex</title>")
    (tmp_path / "embed.html").write_text("<!doctype html><title>widgets</title>")
    (tmp_path / "config.js").write_text("window.REACT_APP_EMBED_ALLOWED_ORIGINS = undefined\n")
    (tmp_path / "assets" / "index-BfLMwcsL.js").write_text("console.log(1)")
    return tmp_path


class TestCluster01ax_SpaDocumentCsp:
    """Cluster 1ax: the SPA document responses `serve_react` itself produces
    carry frame-ancestors -- not just the (unregistered) middleware's responses."""

    def test_1_410_spa_fallback_gets_frame_ancestors_when_origins_set(self, spa_build):
        """
        Scenario 1.410: a client-routed path (no matching file) falls back to
        index.html, which must carry the configured directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS set to two origins
        When: a path with no matching file is served (the SPA fallback)
        Then: the index.html response carries frame-ancestors 'self' <both origins>
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("some/client/route", spa_build)

        assert response["Content-Security-Policy"] == _EXPECTED_FRAME_ANCESTORS

    def test_1_410_literal_html_file_also_gets_frame_ancestors(self, spa_build):
        """
        Scenario 1.410: a real, non-fallback .html file is a document too.
        Given: LEX_EMBED_ALLOWED_ORIGINS set, and embed.html -- a second real Vite
               entry point served as a literal file, not through the SPA fallback
        When: embed.html is served
        Then: it also carries frame-ancestors 'self' <origins> -- the restriction
              covers every HTML document this view serves, not just index.html
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("embed.html", spa_build)

        assert response["Content-Security-Policy"] == _EXPECTED_FRAME_ANCESTORS

    def test_1_410_literal_htm_file_matched_case_insensitively(self, spa_build):
        """
        Scenario 1.410 (minor fix): the document check is case-insensitive and
        also recognises the .htm extension.
        Given: LEX_EMBED_ALLOWED_ORIGINS set, and a file named INDEX.HTM -- an
               unusual case/extension a build could still legitimately emit
        When: it is served as a literal file
        Then: it is still recognised as a document and gets the directive
        """
        (spa_build / "INDEX.HTM").write_text("<!doctype html><title>upper</title>")

        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("INDEX.HTM", spa_build)

        assert response["Content-Security-Policy"] == _EXPECTED_FRAME_ANCESTORS

    def test_1_410_existing_csp_on_spa_fallback_keeps_other_directives(self, spa_build):
        """
        Scenario 1.410: merging the directive never drops what was already there.
        Given: LEX_EMBED_ALLOWED_ORIGINS set, and a response carrying a CSP header
               already. This exact state -- a CSP already present on the response
               `_apply_embed_csp` receives -- cannot happen through serve_react's
               own normal flow: nothing runs between `static_serve` building the
               response and `_apply_embed_csp` seeing it, so in practice that
               argument's CSP header is always empty (see `_apply_embed_csp`'s
               docstring for the real-world gotcha this creates with Django's
               native `SECURE_CSP` instead). `static_serve` is mocked here purely
               to exercise `merge_frame_ancestors`'s general-purpose "preserve
               other directives" behaviour through this call site, the same
               behaviour the middleware's own 1.406 test proves it needs for real.
        When: the SPA fallback response is built
        Then: the original directive survives, and frame-ancestors is merged in
              alongside it
        """
        existing = HttpResponse("<!doctype html>")
        existing["Content-Security-Policy"] = "default-src 'self'"

        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS), \
                mock.patch.object(react_views, "static_serve", return_value=existing):
            response = _serve("some/client/route", spa_build)

        csp = response["Content-Security-Policy"]
        assert "default-src 'self'" in csp, csp
        assert _EXPECTED_FRAME_ANCESTORS in csp, csp

    def test_1_410_hashed_asset_gets_no_frame_ancestors(self, spa_build):
        """
        Scenario 1.410: a hashed asset is not a document and never gets the directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS set, and a content-hashed JS asset
        When: the asset is served
        Then: no Content-Security-Policy header is added -- framing restrictions
              only matter on documents
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("assets/index-BfLMwcsL.js", spa_build)

        assert "Content-Security-Policy" not in response

    def test_1_410_config_js_gets_no_frame_ancestors(self, spa_build):
        """
        Scenario 1.410: config.js is a script, not a document, and never gets the directive.
        Given: LEX_EMBED_ALLOWED_ORIGINS set
        When: config.js is served
        Then: no Content-Security-Policy header is added to it
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=_ALLOWED_ORIGINS):
            response = _serve("config.js", spa_build)

        assert "Content-Security-Policy" not in response

    def test_1_410_spa_fallback_has_no_added_csp_when_unset(self, spa_build):
        """
        Scenario 1.410: unset/empty behaves exactly as before this change.
        Given: LEX_EMBED_ALLOWED_ORIGINS == [] and LEX_EMBED_ORIGINS_CONFIGURED == False
        When: a client-routed path falls back to index.html
        Then: no Content-Security-Policy header is added
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=[], LEX_EMBED_ORIGINS_CONFIGURED=False):
            response = _serve("some/client/route", spa_build)

        assert "Content-Security-Policy" not in response

    def test_1_410_configured_but_all_invalid_still_gets_self_only_directive(self, spa_build):
        """
        Scenario 1.410 (fail closed): set-but-nothing-valid restricts to 'self' here too.
        Given: LEX_EMBED_ALLOWED_ORIGINS == [] but LEX_EMBED_ORIGINS_CONFIGURED == True
               (an operator configured something; none of it validated)
        When: a client-routed path falls back to index.html
        Then: the response carries exactly frame-ancestors 'self' -- the same
              fail-closed outcome TestCluster01ax_MiddlewareAddsFrameAncestors
              pins for the middleware, so a deployment can't end up restricted at
              one layer and wide open at the other
        """
        with override_settings(LEX_EMBED_ALLOWED_ORIGINS=[], LEX_EMBED_ORIGINS_CONFIGURED=True):
            response = _serve("some/client/route", spa_build)

        assert response["Content-Security-Policy"] == _SELF_ONLY_DIRECTIVE
