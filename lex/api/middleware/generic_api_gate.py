"""Who may use lex's generic REST endpoints.

``lex_config.GENERIC_API`` decides access to every ``api/*`` path that is not
part of the project API (``lex_config.API_PREFIX``) or the health check:

* ``"all"`` (default) – everyone the endpoints themselves admit; unchanged behaviour.
* ``"superuser"`` – superusers only; everyone else gets 404, as for a path that
  does not exist. Useful when a project serves its own product API and keeps
  the generic endpoints for operators.
* ``"off"`` – nobody. This also disables the bundled frontend, which is built
  on these endpoints.
"""
from __future__ import annotations

import os

from django.http import JsonResponse

from lex.core import extensions

_ALWAYS_OPEN = ("api/health",)
# The bundled frontend probes the session here and starts the OIDC sign-in on
# 401; an anonymous caller must keep getting the login answer for it.
_SESSION_PROBE = ("api/user/",)


def _base_path() -> str:
    raw = os.getenv("DJANGO_BASE_PATH", "").strip().strip("/")
    return f"/{raw}" if raw else ""


def generic_api_closed_for(request) -> bool:
    mode = extensions.generic_api_mode()
    if mode == "all":
        return False
    base = _base_path()
    path = request.path
    if not path.startswith(f"{base}/api/"):
        return False
    relative = path[len(base) + 1:]
    if relative.startswith(_ALWAYS_OPEN):
        return False
    if extensions.get_setting("API_URLCONF") and relative.startswith(extensions.api_prefix()):
        return False
    user = getattr(request, "user", None)
    signed_in = user is not None and user.is_authenticated
    # The session probe answers 401 to an anonymous caller (it is exempt from
    # the login check), which is how the bundled frontend starts the sign-in.
    if not signed_in and relative.startswith(_SESSION_PROBE):
        return False
    if mode == "off":
        return True
    return not (signed_in and user.is_superuser)


def generic_api_hidden_from_anonymous(request) -> bool:
    """A closed generic path that an anonymous caller should see as missing (404).

    The login check runs before the gate, so without this an anonymous caller
    would be sent to sign in (302/401) and learn that the path exists. The
    session probe of the bundled frontend is not closed for anonymous callers,
    so it keeps its own 401.
    """
    user = getattr(request, "user", None)
    if user is not None and user.is_authenticated:
        return False
    if not generic_api_closed_for(request):
        return False
    return True


class GenericApiGateMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if generic_api_closed_for(request):
            return JsonResponse(
                {"code": "NOT_FOUND", "detail": "Not found.", "error": "Not found."},
                status=404,
            )
        return self.get_response(request)
