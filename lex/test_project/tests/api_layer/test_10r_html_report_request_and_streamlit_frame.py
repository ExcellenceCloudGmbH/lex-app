"""``api/htmlreport/<name>`` hands a report the request, and the Streamlit frame a credential.

Intent
------
The ``Streamlit`` report is a sidebar entry whose whole job is to show the
project's Streamlit app *inside* the instance: the user clicks it, stays on
``https://<instance>.<domain>/streamlit``, and the dashboard renders in a frame
on that page.

The frame is cross-origin -- the dashboard is served from its own host, behind
the auth proxy -- and a cross-origin frame cannot authenticate itself: the proxy
would have to redirect it to Keycloak, which Keycloak forbids by setting
``frame-ancestors 'self'``. Its only way in is the bootstrap ``auth_token`` the
embedding page hands it, which the proxy exchanges for a session cookie on the
first request and then strips from the URL. That is the handshake the React
shell's own dashboard embed has always performed.

Framed without one, the dashboard 401s, and a framed 401 is answered with a page
that moves the *top* window to the dashboard host's login. With an SSO session
live that round trip completes immediately, so the reported fault is a user who
clicked a sidebar entry inside their instance and found the whole tab parked on
``https://<instance>-streamlit.<domain>/?embed=true``. The credential is what
stops it being a 401 in the first place.

``HTMLReport`` has only ever promised ``get_html(self, user)``, and projects
subclass it, so the request reaches only a report that asks for it. A project
report that does not must keep being called exactly as before -- this route is
the one place every report in a project is rendered.

Cluster 10r — scenarios 10.100–10.102. Type: E (10.102 U).
Covers: lex/process_admin/views/model_relation_views.py (Overview,
render_html_report), lex/lex_app/streamlit/Streamlit.py (get_html).
Run: python -m lex pytest lex/test_project/tests/api_layer/test_10r_html_report_request_and_streamlit_frame.py -v
"""

from __future__ import annotations

import os
import re
from unittest import mock
from urllib.parse import parse_qs, urlparse

import pytest
from django.test import SimpleTestCase
from django.urls import reverse

from lex.core.models.HTMLReport import HTMLReport
from lex.lex_app.streamlit.Streamlit import Streamlit
from lex.process_admin.views.model_relation_views import render_html_report
from lex.test_project.tests._e2e_test_case import E2ETestCase

pytestmark = pytest.mark.api_layer

DASHBOARD_HOST = "https://instance-streamlit.example.test"
ACCESS_TOKEN = "header.payload.signature"


def _frame_src(html: str) -> str:
    """The ``src`` of the single iframe in a report's HTML."""
    found = re.findall(r'src="([^"]+)"', html)
    assert len(found) == 1, f"expected exactly one framed URL, got {found!r}"
    return found[0]


class _ReportWithoutRequest(HTMLReport):
    """A project's own report: the published signature, nothing more."""

    seen: list = []

    def get_html(self, user):
        type(self).seen.append((user,))
        return "<p>project report</p>"


class _ReportWithRequest(HTMLReport):
    """A report that asks for the request as well."""

    seen: list = []

    def get_html(self, user, request=None):
        type(self).seen.append((user, request))
        return "<p>asked for the request</p>"


class TestCluster10r_StreamlitFrameCredential(E2ETestCase):
    """Cluster 10r: the dashboard the sidebar entry frames can authenticate."""

    e2e_models: list = []

    def _register(self, name, report):
        from lex.process_admin.settings import processAdminSite

        processAdminSite.html_reports[name] = report
        self.addCleanup(processAdminSite.html_reports.pop, name, None)
        return reverse("process_admin_rest_api:htmlreports", kwargs={"report_name": name})

    def _with_access_token(self, token):
        session = self.client.session
        if token is None:
            session.pop("oidc_access_token", None)
        else:
            session["oidc_access_token"] = token
        session.save()

    # -- 10.100 --------------------------------------------------------
    def test_10_100_the_streamlit_report_frames_the_dashboard_with_a_credential(self) -> None:
        """
        Scenario 10.100: the Streamlit sidebar entry frames a dashboard that can let the user in.
        Given: a signed-in caller whose session holds an OIDC access token, and a
               deployment whose dashboard lives on its own host
        When:  the caller opens api/htmlreport/streamlit -- what the sidebar entry renders
        Then:  the HTML frames that host carrying the session's access token as the
               bootstrap ``auth_token``, so the frame authenticates instead of 401ing
               and sending the whole browser tab to the dashboard host's login --
               and the response carrying that token is not one a cache may keep.
        """
        url = self._register("streamlit", Streamlit)
        self._with_access_token(ACCESS_TOKEN)

        with mock.patch.dict(os.environ, {"STREAMLIT_URL": DASHBOARD_HOST}):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200, msg="the sidebar entry's report must render")
        src = urlparse(_frame_src(response.json()))
        self.assertEqual(
            f"{src.scheme}://{src.netloc}", DASHBOARD_HOST,
            msg="the frame must point at the configured dashboard host",
        )
        self.assertEqual(
            parse_qs(src.query).get("auth_token"), [ACCESS_TOKEN],
            msg="a cross-origin frame cannot authenticate itself -- without the bootstrap "
                "token the proxy 401s it and the 401 takes over the browser tab",
        )
        self.assertIn(
            "no-store", response.headers.get("Cache-Control", ""),
            msg="this response now carries the caller's access token: a report's HTML is "
                "per-user anyway, and one with no Cache-Control may be stored heuristically",
        )

    # -- 10.101 --------------------------------------------------------
    def test_10_101_no_session_token_means_no_token_on_the_url(self) -> None:
        """
        Scenario 10.101: with nothing to hand the frame, nothing is handed to it.
        Given: a signed-in caller whose session carries no OIDC access token
               (an API key, or a session built by another auth backend)
        When:  the caller opens api/htmlreport/streamlit
        Then:  the dashboard is still framed, and the URL carries no ``auth_token``
               at all -- not an empty one and not a stringified placeholder, either
               of which the proxy would have to reject as a malformed credential.
        """
        url = self._register("streamlit", Streamlit)
        self._with_access_token(None)

        with mock.patch.dict(os.environ, {"STREAMLIT_URL": DASHBOARD_HOST}):
            response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        src = urlparse(_frame_src(response.json()))
        self.assertEqual(
            f"{src.scheme}://{src.netloc}", DASHBOARD_HOST,
            msg="the frame must still point at the dashboard host",
        )
        self.assertNotIn(
            "auth_token", parse_qs(src.query),
            msg="an absent credential must be absent, never a placeholder the proxy must parse",
        )

    # -- 10.102 --------------------------------------------------------
    def test_10_102_a_report_is_given_the_request_only_if_it_asks(self) -> None:
        """
        Scenario 10.102: the published ``get_html(self, user)`` contract is unchanged.
        Given: a project report declaring only ``(self, user)`` and one declaring a
               second parameter, both reachable on api/htmlreport/<name>
        When:  each is rendered through the route, and through render_html_report directly
        Then:  the first is called with the user alone -- a project that subclasses
               HTMLReport must not start receiving an argument it never declared --
               while the second is handed the request it asked for.
        """
        _ReportWithoutRequest.seen = []
        _ReportWithRequest.seen = []
        plain = self._register("cluster10rplain", _ReportWithoutRequest)
        asking = self._register("cluster10rasking", _ReportWithRequest)

        # A report that declares only (self, user) raises TypeError if handed a
        # second argument, so rendering it at all is the assertion: a 500 here
        # is every project report in the wild breaking on upgrade.
        self.assertEqual(
            self.client.get(plain).json(), "<p>project report</p>",
            msg="a report that declares only (self, user) must keep being called that way",
        )
        self.assertEqual(self.client.get(asking).json(), "<p>asked for the request</p>")

        self.assertEqual(
            [call[0] for call in _ReportWithoutRequest.seen], [self.user],
            msg="the report is rendered once, for the signed-in caller",
        )
        self.assertEqual(len(_ReportWithRequest.seen), 1, msg="the asking report must be rendered once")
        self.assertIsNotNone(
            _ReportWithRequest.seen[0][1],
            msg="a report that declares a second parameter must receive the request",
        )


class TestCluster10r_RenderDispatch(SimpleTestCase):
    """Cluster 10r: the dispatch rule on its own, away from the route."""

    # -- 10.102 (unit half) --------------------------------------------
    def test_10_102_dispatch_reads_the_signature_not_a_type_error(self) -> None:
        """
        Scenario 10.102: a TypeError raised *inside* a report is not mistaken for a signature.
        Given: a report whose ``get_html(self, user)`` raises TypeError of its own
        When:  it is rendered
        Then:  that TypeError reaches the caller -- guessing the signature by catching
               TypeError would swallow a genuine bug in a project's report and silently
               retry it with an extra argument.
        """

        class _Exploding(HTMLReport):
            def get_html(self, user):
                raise TypeError("the report itself is broken")

        with self.assertRaises(TypeError):
            render_html_report(_Exploding(), object(), object())
