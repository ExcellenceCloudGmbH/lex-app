"""Bridge MCP tool calls into existing DRF view classes.

Each tool dispatches to a real DRF ``APIView`` via a synthetic
:class:`django.http.HttpRequest`. That keeps RBAC
(:class:`lex.api.views.permissions.UserPermission.UserPermission`),
audit logging (:class:`lex.audit_logging.mixins.AuditLogMixin`),
``simple_history`` actor recording, validation and serialization as a
single source of truth — the MCP server never re-implements any of it.

We deliberately set ``Authorization: Bearer mcp`` on the synthetic
request so that
:class:`lex.authentication.authentication_backends.BearerMiddlewareAuthentication.BearerMiddlewareAuthentication`
returns the pre-attached user and DRF skips its CSRF dance for
``SessionAuthentication``.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Mapping, Optional

from asgiref.sync import sync_to_async
from django.http import HttpRequest
from django.test import RequestFactory

from lex.mcp_server.context import McpPrincipal

logger = logging.getLogger(__name__)
_factory: Optional[RequestFactory] = None


def _get_factory() -> RequestFactory:
    global _factory
    if _factory is None:
        _factory = RequestFactory()
    return _factory


def _safe_host() -> str:
    """Pick a hostname accepted by Django's ALLOWED_HOSTS check."""
    from django.conf import settings

    allowed = list(getattr(settings, "ALLOWED_HOSTS", []) or [])
    for host in allowed:
        if not host:
            continue
        if host in ("*", ".*"):
            return "localhost"
        # Strip leading dot used by Django for subdomain wildcards
        # (e.g. ``.example.com``) and any port suffix.
        cleaned = host.lstrip(".")
        if cleaned:
            return cleaned
    return "localhost"


def _attach_principal(request: HttpRequest, principal: McpPrincipal) -> None:
    request.user = principal.user
    request.user_permissions = list(principal.user_permissions)
    request.userinfo = dict(principal.userinfo)
    request.client_roles = list(principal.client_roles)
    request.session = {}
    request._dont_enforce_csrf_checks = True

    # Pre-cache the API-key identity so ``is_api_key_request(request)`` and
    # downstream helpers (LexModel audit, view utils) see the same identity
    # they would for a regular HTTP API-key request, without needing to
    # parse the ``API-KEY`` header off the synthetic request.
    if principal.auth_kind == "api_key" and principal.api_key_name:
        from lex.api.utils.api_key_requests import (
            APIKeyRequestIdentity,
            DEFAULT_API_KEY_SCOPES,
        )

        request._lex_api_key_identity = APIKeyRequestIdentity(
            api_key_name=principal.api_key_name,
            user=principal.user,
            scopes=DEFAULT_API_KEY_SCOPES,
        )


def _build_request(
    method: str,
    *,
    principal: McpPrincipal,
    path: str = "/mcp-internal/",
    query: Optional[Mapping[str, Any]] = None,
    body: Optional[Any] = None,
) -> HttpRequest:
    headers = {
        "HTTP_AUTHORIZATION": "Bearer mcp",
        # ``RequestFactory`` defaults to ``HTTP_HOST=testserver`` which
        # blows up Django's ALLOWED_HOSTS check in non-DEBUG deployments.
        # Pick the first concrete entry from ALLOWED_HOSTS — if it's a
        # wildcard or empty, fall back to ``localhost``.
        "HTTP_HOST": _safe_host(),
    }

    method = method.upper()
    factory = _get_factory()
    if method == "GET":
        request = factory.get(path, data=dict(query or {}), **headers)
    elif method == "DELETE":
        if body is None:
            request = factory.delete(path, **headers)
        else:
            request = factory.delete(
                path,
                data=json.dumps(body, default=str),
                content_type="application/json",
                **headers,
            )
    else:
        payload = "" if body is None else json.dumps(body, default=str)
        request = factory.generic(
            method,
            path,
            data=payload,
            content_type="application/json",
            **headers,
        )

    _attach_principal(request, principal)
    return request


_TEXTUAL_MIME_PREFIXES = ("text/",)
_TEXTUAL_MIME_EXACT = {
    "application/json",
    "application/javascript",
    "application/xml",
    "application/yaml",
    "application/x-yaml",
}


def _is_textual_mime(content_type: str) -> bool:
    """Return True if ``content_type`` should be decoded as JSON/text.

    Treats ``application/*+json|+xml|+yaml`` as textual but is careful
    NOT to do a substring match (xlsx MIME contains the literal
    ``openxmlformats``/``spreadsheetml`` and is binary).
    """
    if not content_type:
        return True
    main = content_type.split(";", 1)[0].strip().lower()
    if not main:
        return True
    if main in _TEXTUAL_MIME_EXACT:
        return True
    if any(main.startswith(prefix) for prefix in _TEXTUAL_MIME_PREFIXES):
        return True
    if main.startswith("application/") and (
        main.endswith("+json") or main.endswith("+xml") or main.endswith("+yaml")
    ):
        return True
    return False


def _parse_content_disposition(header: str) -> Optional[str]:
    """Return the filename advertised by a ``Content-Disposition`` header.

    Honours RFC 5987 ``filename*=UTF-8''…`` over the legacy ``filename=…``
    parameter when both are present.
    """
    if not header:
        return None
    filename: Optional[str] = None
    for raw_part in header.split(";"):
        part = raw_part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        key = key.strip().lower()
        value = value.strip().strip('"')
        if key == "filename*":
            # RFC 5987: charset'lang'percent-encoded
            try:
                _charset, _lang, encoded = value.split("'", 2)
                from urllib.parse import unquote

                return unquote(encoded)
            except ValueError:
                continue
        if key == "filename" and filename is None:
            filename = value
    return filename


def _drain_response_bytes(response) -> bytes:
    """Read all bytes from a Django HTTP/streaming response."""
    streaming = getattr(response, "streaming_content", None)
    if streaming is not None:
        chunks = []
        for chunk in streaming:
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            chunks.append(chunk)
        return b"".join(chunks)
    raw = getattr(response, "content", b"") or b""
    if isinstance(raw, str):
        raw = raw.encode("utf-8")
    return raw


def _close_response(response) -> None:
    """Close a Django response (releases ``FileResponse`` file handles)."""
    closer = getattr(response, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:  # pragma: no cover - defensive
            pass


def _decode_response(response) -> Any:
    """Convert a DRF/Django response into a JSON-serialisable Python object.

    Returns a dict ``{"_binary": True, "content_bytes": …, "content_type": …,
    "filename": …, "status_code": …}`` for binary HTTP responses (xlsx,
    pdf, octet-stream, image/*, etc.). Tools call
    :func:`lex.mcp_server.tools._common.binary_envelope` on that dict to
    produce a base64 envelope (or 413 if over cap).
    """
    if hasattr(response, "data"):
        return response.data

    content_type = ""
    headers = getattr(response, "headers", None)
    if headers is not None:
        try:
            content_type = headers.get("Content-Type", "") or ""
        except Exception:
            content_type = ""
    if not content_type:
        # Older Django responses store header lookups via __getitem__.
        try:
            content_type = response["Content-Type"]
        except Exception:
            content_type = ""

    if _is_textual_mime(content_type):
        raw = _drain_response_bytes(response)
        _close_response(response)
        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except Exception:
            return raw.decode("utf-8", errors="replace")

    raw = _drain_response_bytes(response)
    disposition = ""
    if headers is not None:
        try:
            disposition = headers.get("Content-Disposition", "") or ""
        except Exception:
            disposition = ""
    if not disposition:
        try:
            disposition = response["Content-Disposition"]
        except Exception:
            disposition = ""

    filename = _parse_content_disposition(disposition)
    _close_response(response)

    return {
        "_binary": True,
        "content_bytes": raw,
        "content_type": content_type or "application/octet-stream",
        "filename": filename,
        "status_code": getattr(response, "status_code", 200),
    }


def _call_view_sync(
    view_class,
    *,
    principal: McpPrincipal,
    method: str,
    view_kwargs: Mapping[str, Any],
    query: Optional[Mapping[str, Any]] = None,
    body: Optional[Any] = None,
    pk: Optional[Any] = None,
    view_init_kwargs: Optional[Mapping[str, Any]] = None,
) -> tuple[int, Any]:
    """Run ``view_class.as_view()`` with a synthetic request and return ``(status, data)``.

    ``view_init_kwargs`` is forwarded to ``as_view(...)`` for views that
    declare class-level configuration kwargs (e.g.
    :class:`lex.api.views.global_search_for_models.Search.Search` which
    expects ``model_collection=...``).
    """
    from simple_history.models import HistoricalRecords

    from lex.mcp_server.observability import ToolCallSpan

    path = "/mcp-internal/"
    if pk is not None:
        path = f"/mcp-internal/{pk}/"

    request = _build_request(
        method, principal=principal, path=path, query=query, body=body
    )
    view = view_class.as_view(**(dict(view_init_kwargs) if view_init_kwargs else {}))
    extra = dict(view_kwargs)
    if pk is not None:
        extra.setdefault("pk", pk)

    # Only expose the synthetic request to ``simple_history`` when the
    # principal is backed by a real ``auth.User`` row. ``TechnicalAPIKeyUser``
    # (used for API-key principals) is not a Django model instance, so
    # ``Historical*.history_user`` would reject it as an FK assignment.
    # The regular HTTP API has the same behaviour: API-key requests record
    # ``history_user=NULL``.
    expose_to_history = principal.auth_kind != "api_key"
    if expose_to_history:
        HistoricalRecords.context.request = request
    try:
        with ToolCallSpan(view_class.__name__, principal=principal):
            response = view(request, **extra)
            if hasattr(response, "render"):
                try:
                    response.render()
                except Exception:
                    pass
            return response.status_code, _decode_response(response)
    finally:
        if expose_to_history and hasattr(HistoricalRecords.context, "request"):
            try:
                del HistoricalRecords.context.request
            except AttributeError:
                pass


call_view = sync_to_async(_call_view_sync, thread_sensitive=True)
