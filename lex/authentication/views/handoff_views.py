"""``GET oidc/handoff`` -- the app's half of the Streamlit dashboard hand-off.

The dashboard proxy sends the browser here, inside the dashboard's frame::

    /oidc/handoff?aud=streamlit&return=<dashboard URL>&state=<nonce>

* **Signed in** (an active ``OIDCSession``) -- a single-use code is minted, valid 60 s and bound
  to the sign-in, the audience, the dashboard origin and ``state``, and the browser goes back to
  ``<dashboard origin>/auth/handoff?code=…&state=…&next=<path>``.
* **Not signed in, in a frame** -- back to ``<dashboard origin>/auth/handoff?error=login_required``.
  Keycloak never renders in a frame (it forbids it); the dashboard tells the frontend around it,
  which renews its own sign-in and reloads the dashboard.
* **Not signed in, top level** -- the normal sign-in (``oidc/authenticate?next=`` this URL), then
  the hand-off continues.

``return`` must be an origin the instance lists for its dashboards (``LEX_INSTANCE_HOSTS``,
loopback in development); anything else is a 400, so the view never redirects to a host it does
not know. The view is exempt from the login requirement and from the session middleware: it
loads and checks the sign-in itself (with the library's own functions), so an ended sign-in in a
frame still goes back to the dashboard as ``login_required`` instead of a sign-in page in the
frame. Keycloak being unreachable answers the contract's 503.

Only served while ``LEX_STREAMLIT_AUTH`` is ``dual`` or ``handoff``; 404 otherwise.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import urlencode, urlsplit

from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.clickjacking import xframe_options_exempt
from oauth2_authcodeflow import responses as oidc_responses
from oauth2_authcodeflow import sessions as oidc_sessions
from oauth2_authcodeflow.exceptions import OIDCAuthUnavailable, OIDCSessionEnded

from lex.authentication.dashboards import modes, sites
from lex.authentication.dashboards.handoff import AUD_STREAMLIT, issue_code

logger = logging.getLogger(__name__)

#: A ``state`` nonce as the proxy makes them (URL-safe, 16-128 characters).
STATE_PATTERN = re.compile(r"^[A-Za-z0-9_\-]{16,128}$")


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = "no-referrer"
    return response


def _bad_request(detail: str) -> HttpResponse:
    return _no_store(JsonResponse({"error": "invalid_request", "detail": detail}, status=400))


def _relative_target(target: str) -> str:
    """The path and query of the dashboard URL, as a same-origin relative target ("/" if unsafe)."""
    parts = urlsplit(target)
    path = parts.path or "/"
    if not path.startswith("/") or path.startswith("//") or "\\" in path:
        return "/"
    return path + (f"?{parts.query}" if parts.query else "")


@method_decorator(xframe_options_exempt, name="dispatch")
class HandoffView(View):
    """``GET oidc/handoff``: see the module docstring."""

    http_method_names = ["get"]

    def get(self, request):
        if not modes.handoff_enabled(modes.streamlit_auth_mode()):
            return _no_store(JsonResponse({"error": "not_found", "detail": "The dashboard hand-off is off."}, status=404))
        if request.GET.get("aud") != AUD_STREAMLIT:
            return _bad_request("aud must be 'streamlit'.")
        state = request.GET.get("state", "")
        if not STATE_PATTERN.match(state):
            return _bad_request("state is missing or malformed.")
        target = request.GET.get("return", "")
        origin = sites.origin_of(target)
        if origin is None or not sites.is_dashboard_origin(origin):
            logger.warning("Dashboard hand-off refused: return origin %r is not a dashboard of this instance", origin)
            return _bad_request("return is not a dashboard of this instance.")
        next_path = _relative_target(target)

        row = None
        try:
            row = oidc_sessions.load_for_request(request)
            if row is not None:
                row = oidc_sessions.ensure_fresh(row)
                oidc_sessions.renew_django_session(request, row)
        except OIDCSessionEnded as exc:
            oidc_sessions.logout_request(request, exc.reason)
            row = None
        except OIDCAuthUnavailable as exc:
            return _no_store(oidc_responses.auth_unavailable(request, exc))

        if row is None:
            kind = oidc_responses.request_kind(request)
            if kind == oidc_responses.FRAME:
                query = urlencode({"error": "login_required", "state": state, "next": next_path})
                return _no_store(HttpResponseRedirect(f"{origin}/auth/handoff?{query}"))
            if kind == oidc_responses.NAVIGATION:
                return _no_store(HttpResponseRedirect(oidc_responses.login_url(request.get_full_path())))
            return _no_store(oidc_responses.unauthenticated(request, "not_authenticated"))

        code = issue_code(row, aud=AUD_STREAMLIT, return_origin=origin, state=state)
        query = urlencode({"code": code, "state": state, "next": next_path})
        return _no_store(HttpResponseRedirect(f"{origin}/auth/handoff?{query}"))


__all__ = ["HandoffView", "STATE_PATTERN"]
