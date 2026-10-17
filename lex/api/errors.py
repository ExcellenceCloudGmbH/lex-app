"""Error bodies of the lex REST API.

* :func:`error_detail` – removes the server traceback from an error body unless
  tracebacks may be shown (``DEBUG``, ``LEX_API_TRACEBACKS=true`` or the legacy
  security opt-out; see :mod:`lex.core.security_flags`).
* :class:`LexActionError` – a business error with a stable ``code``, raised by
  project endpoints and record actions.
* :func:`lex_exception_handler` – the DRF exception handler lex installs.
  Without configuration it returns exactly what DRF returns. It adds
  ``LexActionError`` rendering, the ``DB_ERROR_MAPPER`` hook, the opt-in
  structured format (``LEX_ERROR_FORMAT=structured``) and a project override
  (``lex_config.EXCEPTION_HANDLER``).

Every body keeps an ``error`` or ``detail`` key, because that is what the
bundled frontend reads first.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Mapping, Optional

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.db import DatabaseError
from django.http import Http404
from rest_framework import exceptions, status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler

from lex.core.security_flags import expose_tracebacks

logger = logging.getLogger(__name__)


def error_detail(body: Mapping[str, Any]) -> Dict[str, Any]:
    """Return ``body`` without its ``traceback`` key unless tracebacks may be shown."""
    cleaned = dict(body)
    if "traceback" in cleaned and not expose_tracebacks():
        cleaned.pop("traceback")
    return cleaned


class LexActionError(Exception):
    """A business error with a stable, machine-readable ``code``.

    Rendered as ``{"code", "detail", "error", "fields"}``; ``error`` repeats
    ``detail`` for clients that read that key. ``fields`` maps field names to
    messages and is omitted when empty.
    """

    def __init__(
        self,
        detail: str,
        *,
        code: str = "INVALID_REQUEST",
        fields: Optional[Mapping[str, Any]] = None,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        **extra: Any,
    ) -> None:
        super().__init__(detail)
        self.detail = detail
        self.code = code
        self.fields = dict(fields or {})
        self.status_code = status_code
        self.extra = extra

    def as_body(self) -> Dict[str, Any]:
        body: Dict[str, Any] = {"code": self.code, "detail": self.detail, "error": self.detail}
        if self.fields:
            body["fields"] = self.fields
        body.update(self.extra)
        return body

    def as_response(self) -> Response:
        return Response(self.as_body(), status=self.status_code)


_DEFAULT_CODES = {
    exceptions.ValidationError: "VALIDATION_ERROR",
    exceptions.ParseError: "PARSE_ERROR",
    exceptions.NotAuthenticated: "NOT_AUTHENTICATED",
    exceptions.AuthenticationFailed: "AUTHENTICATION_FAILED",
    exceptions.PermissionDenied: "PERMISSION_DENIED",
    exceptions.NotFound: "NOT_FOUND",
    exceptions.MethodNotAllowed: "METHOD_NOT_ALLOWED",
    exceptions.NotAcceptable: "NOT_ACCEPTABLE",
    exceptions.UnsupportedMediaType: "UNSUPPORTED_MEDIA_TYPE",
    exceptions.Throttled: "THROTTLED",
}


def structured_format() -> bool:
    return os.getenv("LEX_ERROR_FORMAT", "").strip().lower() == "structured"


def _code_for(exc: Exception) -> str:
    for exc_type, code in _DEFAULT_CODES.items():
        if isinstance(exc, exc_type):
            return code
    if isinstance(exc, exceptions.APIException):
        return str(getattr(exc, "default_code", "error")).upper()
    return "INTERNAL_ERROR"


def _first_message(value: Any) -> str:
    if isinstance(value, (list, tuple)) and value:
        return _first_message(value[0])
    if isinstance(value, dict) and value:
        return _first_message(next(iter(value.values())))
    return str(value)


def restructure_response(exc: Exception, response: Response) -> Response:
    """Rewrite an error response into ``{code, detail, error, fields}``."""
    data = response.data
    fields: Dict[str, Any] = {}
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        detail = str(data["detail"])
    elif isinstance(data, dict) and isinstance(exc, exceptions.ValidationError):
        fields = {key: _first_message(value) for key, value in data.items()}
        detail = fields.get("non_field_errors") or "Some fields are invalid."
    elif isinstance(data, dict) and ("error" in data or "message" in data):
        detail = str(data.get("error") or data.get("message"))
    else:
        detail = _first_message(data) if data else "The request could not be processed."
    body: Dict[str, Any] = {"code": _code_for(exc), "detail": detail, "error": detail}
    if fields:
        body["fields"] = fields
    if isinstance(data, dict) and "traceback" in data and expose_tracebacks():
        body["traceback"] = data["traceback"]
    response.data = body
    return response


def map_database_error(exc: Exception) -> Optional[LexActionError]:
    """Run ``lex_config.DB_ERROR_MAPPER`` for a database error, if configured."""
    if not isinstance(exc, DatabaseError):
        return None
    from lex.core.extensions import get_hook

    mapper = get_hook("DB_ERROR_MAPPER")
    if mapper is None:
        return None
    mapped = mapper(exc)
    if mapped is not None and not isinstance(mapped, LexActionError):
        raise TypeError("DB_ERROR_MAPPER must return a LexActionError or None")
    return mapped


_STATUS_CODES = {
    400: "INVALID_REQUEST",
    401: "NOT_AUTHENTICATED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
}


def errors_raised() -> bool:
    """Whether generic endpoints raise their errors to the handler (``LEX_SEC_ERRORS``)."""
    from lex.core.security_flags import strict

    return strict("ERRORS")


def error_from_body(data: Any, status_code: int) -> LexActionError:
    """The ``LexActionError`` for an error body a generic view used to return.

    Every key of the old body is kept, so a client reading ``message``, ``ids``
    or ``available`` still finds it; ``code``, ``detail`` and ``error`` are
    added where the old body lacked them.
    """
    body: Dict[str, Any] = dict(data) if isinstance(data, Mapping) else {"detail": _first_message(data)}
    detail = body.pop("detail", None) or body.get("error") or body.get("message") or "The request could not be processed."
    if body.get("error") == detail:
        body.pop("error")
    code = body.pop("code", None) or _STATUS_CODES.get(status_code, "INVALID_REQUEST")
    fields = body.pop("fields", None)
    return LexActionError(str(detail), code=str(code), fields=fields, status_code=status_code, **body)


def raise_or_return(response: Optional[Response]) -> Optional[Response]:
    """An error ``Response`` built by a generic view: raised (``LEX_SEC_ERRORS``) or returned as before."""
    if response is None or not errors_raised():
        return response
    raise error_from_body(getattr(response, "data", None), response.status_code)


def surface(exc: Exception) -> None:
    """Re-raise ``exc`` when the exception handler should render it instead of a wrapping 500.

    Called in the ``except`` blocks of the generic views before they wrap an
    error into ``APIException``. With ``LEX_SEC_ERRORS`` a business error
    (``LexActionError``), a DRF error (404, 400, ...), Django's ``Http404`` and
    ``PermissionDenied`` and a database error that ``DB_ERROR_MAPPER`` maps
    reach the handler unchanged; so does any database error when the project
    has its own ``EXCEPTION_HANDLER``. Everything else is wrapped as before.
    """
    if not errors_raised():
        return
    if isinstance(exc, (LexActionError, exceptions.APIException, Http404, DjangoPermissionDenied)):
        raise exc
    if isinstance(exc, DatabaseError):
        mapped = map_database_error(exc)
        if mapped is not None:
            raise mapped from exc
        from lex.core.extensions import get_hook

        if get_hook("EXCEPTION_HANDLER") is not None:
            raise exc


def lex_exception_handler(exc: Exception, context: Dict[str, Any]) -> Optional[Response]:
    """DRF exception handler installed by lex (``REST_FRAMEWORK["EXCEPTION_HANDLER"]``)."""
    from lex.core.extensions import get_hook

    override = get_hook("EXCEPTION_HANDLER")
    if override is not None:
        return override(exc, context)

    if isinstance(exc, LexActionError):
        return exc.as_response()

    view = context.get("view")
    structured = structured_format()
    if isinstance(exc, DatabaseError) and (structured or getattr(view, "lex_maps_db_errors", False)):
        mapped = map_database_error(exc)
        if mapped is not None:
            return mapped.as_response()

    response = drf_exception_handler(exc, context)
    if response is None:
        if structured or getattr(view, "lex_structured_errors", False):
            logger.exception("Unhandled error in %s", type(view).__name__ if view else "view")
            body: Dict[str, Any] = {
                "code": "INTERNAL_ERROR",
                "detail": "Something went wrong on our side.",
                "error": "Something went wrong on our side.",
            }
            if expose_tracebacks():
                import traceback

                body["traceback"] = "".join(traceback.format_exception(exc))
            return Response(body, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return None
    if structured or getattr(view, "lex_structured_errors", False):
        return restructure_response(exc, response)
    return response
