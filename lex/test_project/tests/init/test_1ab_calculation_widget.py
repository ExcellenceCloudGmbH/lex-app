"""The Streamlit calculation widget and its backend client.

Intent: a dashboard author should be able to trigger one calculation and watch
it, without embedding a React table just to click one button. The widget talks
to the backend over HTTP as the signed-in user -- never the ORM -- so read
permission, audit actor resolution and the ``_defer_calculate_hook`` trigger
path stay identical to the React UI's. A second way to start a calculation is
what produced the ``edited_at`` bug (PR #675), and an in-process ORM call would
be exactly that.

Three regressions these scenarios exist to prevent, all of them silent:

* **Polling that never stops.** A dashboard left open would keep asking the
  backend for a status that will never change again -- permanent load nobody
  notices, multiplied by every open tab.
* **A failure path that raises.** Streamlit renders top-to-bottom, so an
  exception escaping the widget erases every widget below it on the page. The
  page does not report an error; it silently loses its bottom half.
* **Colour drift.** LEX success is teal, not green. A hex literal here would
  keep rendering the old palette after the next token refresh, and nothing
  would fail.

Cluster 1ab -- scenarios 1.223-1.241. Type: U.
Covers: lex/lex_app/streamlit/_client.py, lex/lex_app/streamlit/calculation.py,
        lex/lex_app/streamlit/__init__.py.
Run: python -m lex pytest lex/test_project/tests/init/test_1ab_calculation_widget.py -v
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests
from django.test import SimpleTestCase

pytestmark = pytest.mark.init


class TestCluster01ab_CalculationClient(SimpleTestCase):
    """Cluster 1ab: the widget's HTTP client."""

    def test_1_223_reuses_the_frontend_origin_the_embed_helper_already_needs(self):
        """
        Scenario 1.223: no new configuration is needed to reach the backend.
        Given: only REACT_APP_URL, the variable lex_view already requires
        When: the client resolves its base URL
        Then: it targets that origin. Django serves the React bundle and /api
              from the same host (lex_app/urls.py routes /api/... and then falls
              through to serve_react), so any deployment that can already embed
              a lex_view can reach the API with no chart change and no new
              secret -- and the two helpers cannot be pointed at different hosts
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {"REACT_APP_URL": "https://demo-prod-4.lex.example.com/"},
            clear=True,
        ):
            self.assertEqual(
                _client.resolve_api_base_url(),
                "https://demo-prod-4.lex.example.com",
                msg=(
                    "The API base must follow the frontend origin lex_view "
                    "already resolves, with the trailing slash trimmed so paths "
                    "do not concatenate into a double slash."
                ),
            )

    def test_1_224_explicit_override_wins(self):
        """
        Scenario 1.224: a deployment can point the widget elsewhere.
        Given: LEX_API_URL set alongside the frontend origin
        When: the client resolves its base URL
        Then: the override wins, matching the precedence
              embed._resolve_base_url already uses -- a split deployment, where
              the API is not on the frontend host, stays reachable without
              patching the framework
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict(
            "os.environ",
            {
                "LEX_API_URL": "http://localhost:9999",
                "REACT_APP_URL": "https://frontend.example.com",
            },
            clear=True,
        ):
            self.assertEqual(
                _client.resolve_api_base_url(),
                "http://localhost:9999",
                msg=(
                    "LEX_API_URL must take precedence over the frontend origin; "
                    "otherwise a split deployment has no way to redirect the "
                    "widget."
                ),
            )

    def test_1_225_an_unreachable_backend_surfaces_as_a_lex_api_error(self):
        """
        Scenario 1.225: transport failures arrive in one catchable shape.
        Given: a backend that cannot be reached at all
        When: the client issues a call
        Then: LexApiError, not requests' own exception. The widget catches
              exactly one type; a raw ConnectionError escaping it would reach
              Streamlit and erase everything rendered below the widget
        """
        from lex.lex_app.streamlit import _client

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch(
                "requests.get", side_effect=requests.ConnectionError("no route to host"),
            ):
                with self.assertRaises(_client.LexApiError) as caught:
                    _client.get_json("/api/thing", token="t")

        self.assertIsNone(
            caught.exception.status,
            msg=(
                "A request that never reached the server has no HTTP status. "
                f"Got {caught.exception.status!r}, which the widget would "
                "translate into a message about a response it never received."
            ),
        )

    def test_1_226_an_error_response_carries_its_status_and_detail(self):
        """
        Scenario 1.226: the caller can tell 403 from 404 from 500.
        Given: a backend answering with an error status and a DRF detail body
        When: the client issues a call
        Then: LexApiError carries both. The widget's whole failure vocabulary is
              built on the status code, and the detail is the only text that can
              explain the refusal to the user
        """
        from lex.lex_app.streamlit import _client

        response = mock.Mock(
            status_code=403,
            content=b'{"detail": "You do not have permission."}',
        )
        response.json.return_value = {"detail": "You do not have permission."}

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch("requests.get", return_value=response):
                with self.assertRaises(_client.LexApiError) as caught:
                    _client.get_json("/api/thing", token="t")

        self.assertEqual(
            caught.exception.status, 403,
            msg=(
                "The HTTP status must survive onto the error, or the widget "
                f"cannot distinguish a refusal from an outage. Got "
                f"{caught.exception.status!r}."
            ),
        )
        self.assertIn(
            "You do not have permission.", str(caught.exception),
            msg=(
                "The backend's own detail is the only text that can explain "
                f"this to the user. Got {str(caught.exception)!r}."
            ),
        )

    def test_1_227_every_call_is_made_as_the_signed_in_user(self):
        """
        Scenario 1.227: the client authenticates as the user, not as the host.
        Given: the signed-in user's access token
        When: the client reads and writes
        Then: both carry it as a bearer token. This is the whole reason the
              widget goes over HTTP instead of touching the ORM: the backend
              resolves read permission and the audit actor from this header, so
              an unauthenticated -- or host-authenticated -- call would run
              somebody else's calculation under the wrong name
        """
        from lex.lex_app.streamlit import _client

        response = mock.Mock(status_code=200, content=b"{}")
        response.json.return_value = {}

        with mock.patch.dict("os.environ", {"LEX_API_URL": "http://api"}, clear=True):
            with mock.patch("requests.get", return_value=response) as get:
                _client.get_json("/api/thing", token="user-token")
            with mock.patch("requests.patch", return_value=response) as patch_call:
                _client.patch_json("/api/thing", token="user-token")

        for name, call in (("GET", get), ("PATCH", patch_call)):
            headers = call.call_args.kwargs["headers"]
            self.assertEqual(
                headers.get("Authorization"), "Bearer user-token",
                msg=(
                    f"The {name} must be made as the signed-in user. Got "
                    f"{headers.get('Authorization')!r} -- without it the "
                    "backend cannot apply that user's permissions or record "
                    "them as the actor."
                ),
            )
