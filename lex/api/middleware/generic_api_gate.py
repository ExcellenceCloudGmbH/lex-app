"""Who may use lex's generic REST endpoints.

``lex_config.GENERIC_API`` decides access to every ``api/*`` path that is not
part of the project API (``lex_config.API_PREFIX``) or the health check:

* ``"all"`` (default) – everyone the endpoints themselves admit; unchanged behaviour.
* ``"records"`` – the record routes (list, one, history, actions, bulk, export,
  file download) of models that set ``lex_api_exposed`` for every signed-in
  caller and API key, limited to the operations the model names; everything
  else (lex's internal endpoints, other models, the history tables, Django's
  users) for superusers only, 404 for everyone else. For a project whose own
  clients use the generic API.
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

#: Operations a model can expose with ``lex_api_exposed`` (mode ``"records"``).
GENERIC_OPERATIONS = ("list", "retrieve", "create", "update", "delete", "history", "export", "bulk", "file")
#: ``lex_api_exposed = True``: the record operations; history, export, bulk and
#: file download are named explicitly because they show more than a record.
DEFAULT_EXPOSED = frozenset({"list", "retrieve", "create", "update", "delete"})

# URL name of a generic route -> operation ("record" is decided by the method).
_ROUTE_OPERATIONS = {
    "model-entries-list": "list",
    "model-one-entry-create": "create",
    "model-one-entry-read-update-delete": "record",
    "model-one-entry-by-lookup": "record",
    "model-history-list": "history",
    "model-history-list-by-lookup": "history",
    "model-record-action": "action",
    "model-record-action-by-lookup": "action",
    "model-collection-action": "action",
    "model-many-entries": "bulk",
    "model-export": "export",
    "file-download": "file",
}
_RECORD_METHODS = {"GET": "retrieve", "HEAD": "retrieve", "PATCH": "update", "PUT": "update", "DELETE": "delete"}


def exposed_operations(model) -> frozenset:
    """The operations ``model`` exposes in mode ``"records"`` (``lex_api_exposed``)."""
    value = getattr(model, "lex_api_exposed", False)
    if value is True:
        return DEFAULT_EXPOSED
    if not value:
        return frozenset()
    return frozenset(value)


def record_operation(request):
    """``(model class, operation)`` of a generic record route, or ``None`` for any other path."""
    from django.urls import Resolver404, resolve

    try:
        match = resolve(request.path_info)
    except Resolver404:
        return None
    name = (match.url_name or "").removesuffix("-short")
    operation = _ROUTE_OPERATIONS.get(name)
    container = match.kwargs.get("model_container")
    if operation is None or container is None:
        return None
    if operation == "record":
        operation = _RECORD_METHODS.get(request.method.upper())
    return container.model_class, operation


def records_open_for(request) -> bool:
    """Mode ``"records"``: the request is an operation its model exposes (actions decide for themselves)."""
    found = record_operation(request)
    if found is None:
        return False
    model, operation = found
    exposed = exposed_operations(model)
    if not exposed or operation is None:
        return False
    return operation == "action" or operation in exposed
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
    if signed_in and user.is_superuser:
        return False
    if mode == "records" and records_open_for(request):
        return False
    return True


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


class GenericApiSocketGate:
    """Websockets follow ``GENERIC_API``: in every mode but ``"all"`` only superusers connect.

    The sockets push model and calculation events of every model; the HTTP gate
    does not see them. ASGI middleware, inside channels' ``AuthMiddlewareStack``.
    """

    def __init__(self, inner):
        self.inner = inner

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "websocket" and extensions.generic_api_mode() != "all":
            user = scope.get("user")
            if not (user is not None and getattr(user, "is_authenticated", False) and user.is_superuser):
                await receive()  # websocket.connect
                await send({"type": "websocket.close", "code": 4404})
                return None
        return await self.inner(scope, receive, send)


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
