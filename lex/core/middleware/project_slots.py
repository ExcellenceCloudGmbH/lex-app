"""Middleware slots for project code.

lex owns ``MIDDLEWARE``; a project adds its own middleware through
``lex_config.MIDDLEWARE_EXTRA`` instead of overriding the setting::

    MIDDLEWARE_EXTRA = {
        "first": ["myproject.security.HeadersMiddleware"],
        "after_auth": ["myproject.auth.ServiceUserMiddleware"],
        "last": [],
    }

Each slot sits at a fixed position in lex's chain:

* ``first``      – right after Django's ``SecurityMiddleware`` and
  ``GZipMiddleware``, so even early answers (401, 404 from later middleware)
  pass through it.
* ``after_auth`` – after session and bearer authentication, before lex's login
  check: ``request.user`` is known and the middleware may still authenticate
  the request itself.
* ``last``       – at the end of the chain, after Keycloak permissions are loaded.

A slot with nothing configured passes the request straight through. The chain
is built when Django loads middleware (after the app registry is ready), so
``lex_config.py`` may import project code.
"""
from __future__ import annotations

from typing import Callable

from django.http import HttpRequest, HttpResponse

from lex.core.extensions import middleware_extra


class _ProjectSlot:
    slot: str = ""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        handler = get_response
        # Build inside-out so the first entry in the list runs first.
        for middleware_class in reversed(middleware_extra(self.slot)):
            handler = middleware_class(handler)
        self._handler = handler

    def __call__(self, request: HttpRequest) -> HttpResponse:
        return self._handler(request)


class ProjectFirstSlot(_ProjectSlot):
    slot = "first"


class ProjectAfterAuthSlot(_ProjectSlot):
    slot = "after_auth"


class ProjectLastSlot(_ProjectSlot):
    slot = "last"
