"""The scope a request acts in, for example the organisation a user works as.

``lex_config.REQUEST_SCOPE = {"param": "organisation", "resolver": "app.scope.resolve"}``

* the query parameter (``?organisation=<id>``) is reserved: it is never read
  as a list filter;
* ``resolver(request, value)`` returns the scope (any object) or ``None``,
  and raises ``NotFound``/``Http404`` for a value the caller may not use —
  the request then answers 404. It is called with ``value=None`` when the
  parameter is absent, so it can choose a default (an API key's
  organisation);
* the scope is ``request.lex_scope``; ``DB_CONTEXT`` receives it as
  ``scope=`` when its signature accepts that keyword, so policies and views
  can read it;
* a model's ``lex_scope_queryset(queryset, scope, request)`` classmethod
  narrows its lists, records, history and action lookups — a row outside
  the scope answers 404.
"""
from __future__ import annotations

from typing import Any, Optional

_UNSET = object()


def config() -> Optional[dict]:
    from django.core.exceptions import ImproperlyConfigured

    from lex.core.extensions import get_setting

    value = get_setting("REQUEST_SCOPE")
    if value is None:
        return None
    if not isinstance(value, dict) or not isinstance(value.get("param"), str) or not value.get("resolver"):
        raise ImproperlyConfigured('lex_config.REQUEST_SCOPE must be {"param": "<name>", "resolver": "<dotted path>"}')
    return value


def param() -> Optional[str]:
    current = config()
    return current["param"] if current else None


def resolver():
    from lex.core.extensions import _resolve

    current = config()
    return _resolve("REQUEST_SCOPE", current["resolver"]) if current else None


def _raw_request(request):
    return getattr(request, "_request", request)


def resolve(request) -> Any:
    """The request's scope (resolved once per request); raises NotFound for a refused value."""
    raw = _raw_request(request)
    cached = getattr(raw, "lex_scope", _UNSET)
    if cached is not _UNSET:
        return cached
    current = config()
    if current is None:
        raw.lex_scope = None
        return None
    value = raw.GET.get(current["param"]) or None
    scope = resolver()(request, value)
    raw.lex_scope = scope
    return scope


def scope_queryset(model, queryset, request):
    """``queryset`` narrowed by the model's ``lex_scope_queryset`` (unchanged without one)."""
    hook = getattr(model, "lex_scope_queryset", None)
    if hook is None:
        return queryset
    return hook(queryset, resolve(request), request)


def apply_context(request) -> None:
    """Re-apply ``DB_CONTEXT`` with the request's scope (no-op without a scope)."""
    from lex.core import db_context

    if config() is None:
        return
    db_context.apply(getattr(request, "user", None), local=True, scope=resolve(request))
