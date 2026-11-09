"""Endpoints of the HTTP auth contract v2 that browser clients call directly.

* ``GET api/auth/session`` (:class:`SessionStatusView`) -- is this browser signed in, as whom,
  and what does the server support? 200 ``{"authenticated": true, "user": {...},
  "capabilities": {...}}``, else the contract's 401 (or, from the session middleware, 503).
  A client that gets 404 knows the server predates the contract.
* ``GET oidc/auth-complete?m=silent|popup`` (:class:`AuthCompleteView`) -- where a silent
  (``prompt=none``, in a hidden iframe) or popup sign-in ends. The page tells the window that
  started it how it went: ``{"type": "lex-auth", "v": 2, "event": "result", "mode": ...,
  "ok": ..., "error"?: ...}``, posted to its opener and its parent -- restricted to its own
  origin -- and on ``BroadcastChannel('lex-auth')``. It carries no token.

Both are exempt from the login requirement (``OIDC_LOGIN_REQUIRED_EXEMPT_URL_PATTERNS``):
they are how a signed-out client finds out it is signed out.
"""
from __future__ import annotations

import json
import re

from django.http import HttpResponse, JsonResponse
from django.views import View

from oauth2_authcodeflow import responses as oidc_responses

from lex.authentication.capabilities import auth_capabilities
from lex.authentication.identity import user_payload

#: The modes a client starts a sign-in in.
AUTH_COMPLETE_MODES = ("silent", "popup")

_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _no_store(response: HttpResponse) -> HttpResponse:
    response["Cache-Control"] = "no-store"
    return response


class SessionStatusView(View):
    """``GET api/auth/session``: sign-in status and server capabilities (always JSON)."""

    http_method_names = ["get", "head"]

    def get(self, request):
        user = getattr(request, "user", None)
        if user is None or not user.is_authenticated:
            response = JsonResponse(
                {"error": "not_authenticated", "login_url": oidc_responses.login_path()}, status=401
            )
            response["WWW-Authenticate"] = oidc_responses.www_authenticate(request)
            return _no_store(response)
        return _no_store(
            JsonResponse(
                {"authenticated": True, "user": user_payload(request), "capabilities": auth_capabilities()}
            )
        )


def auth_result(request) -> dict:
    """The result message of the sign-in that just ended on ``oidc/auth-complete``."""
    mode = request.GET.get("m")
    if mode not in AUTH_COMPLETE_MODES:
        mode = "popup"
    # The client marks its fail URL with error=1; the library appends its own error code
    # (e.g. login_required after prompt=none).
    reported = request.GET.getlist("error")
    user = getattr(request, "user", None)
    signed_in = bool(user is not None and user.is_authenticated)
    ok = signed_in and not reported
    message = {"type": "lex-auth", "v": 2, "event": "result", "mode": mode, "ok": ok}
    if not ok:
        codes = [code for code in reported if code != "1" and _ERROR_CODE.match(code)]
        message["error"] = codes[-1] if codes else ("login_required" if not signed_in else "failed")
    return message


_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>{title}</title>
<meta name="viewport" content="width=device-width, initial-scale=1"></head>
<body style="font-family:sans-serif;margin:2rem"><p>{text}</p>
<script>
(function () {{
  var message = {message};
  var origin = window.location.origin;
  function post(target) {{
    try {{ if (target && target !== window) target.postMessage(message, origin); }} catch (e) {{}}
  }}
  post(window.opener);
  post(window.parent);
  try {{
    if (typeof BroadcastChannel === "function") {{
      var channel = new BroadcastChannel("lex-auth");
      channel.postMessage(message);
      channel.close();
    }}
  }} catch (e) {{}}
  if (message.mode === "popup" && message.ok) {{
    setTimeout(function () {{ window.close(); }}, 100);
  }}
}})();
</script>
</body></html>
"""


class AuthCompleteView(View):
    """``GET oidc/auth-complete?m=silent|popup``: report a finished sign-in to the page that started it."""

    http_method_names = ["get"]

    def get(self, request):
        message = auth_result(request)
        if message["ok"]:
            title, text = "Signed in", "You are signed in. You can close this window."
        else:
            title, text = "Sign-in did not complete", "Sign-in did not complete. You can close this window."
        # JSON inside a <script>: no "</" can end the element early.
        payload = json.dumps(message, sort_keys=True).replace("<", "\\u003c")
        response = HttpResponse(
            _PAGE.format(title=title, text=text, message=payload), content_type="text/html; charset=utf-8"
        )
        # The silent attempt runs in a hidden same-origin iframe (DENY would blank it).
        response["X-Frame-Options"] = "SAMEORIGIN"
        response["Referrer-Policy"] = "no-referrer"
        return _no_store(response)


__all__ = ["AUTH_COMPLETE_MODES", "AuthCompleteView", "SessionStatusView", "auth_result"]
