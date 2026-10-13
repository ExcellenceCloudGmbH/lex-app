"""Record which product area each successful API request used, per actor and UTC day.

Fleet metrics, phase 1: K1 active people, and the ``api`` channel of K2 feature reach,
which is supporting detail beside the interface's own area events (spec §5.3).

The middleware is the last entry in ``MIDDLEWARE``. So the user it reads is the one every
earlier middleware settled on, and it acts only after the view has returned. It never
changes the response. The storage layer pauses itself on a database error, and the
lex-analytics collector notices a silent recorder from the data (spec §7).

What is recorded:

* only a response below 400, to a method other than OPTIONS or HEAD, on a route with a
  URL name;
* a person: an authenticated Django user, by id;
* an API key: by the key's name, on this channel only.

Never recorded: the instance's own platform key (``LEX_API_KEY``, the Instance
Controller's), an anonymous request, an unnamed route, and ``POST api/usage/area``. That
endpoint records its event on an interface channel itself; ``area_for`` returns None for
it.

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3.1)
"""

from __future__ import annotations

import logging
from typing import Optional

from lex.api.utils.api_key_requests import (
    get_api_key_request_identity,
    is_instance_api_key_request,
)
from lex.core.services.usage_areas import area_for
from lex.core.services.usage_metrics import API_KEY, CHANNEL_API, PERSON, recorder

logger = logging.getLogger(__name__)

_SKIPPED_METHODS = frozenset({"OPTIONS", "HEAD"})


def actor_for(request) -> Optional[tuple[str, str]]:
    """``(kind, id)`` for whoever made ``request``, or None when nobody is attributed."""
    if is_instance_api_key_request(request):
        return None
    identity = get_api_key_request_identity(request)
    if identity is not None:
        return (API_KEY, identity.api_key_name)
    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False) and getattr(user, "pk", None) is not None:
        return (PERSON, str(user.pk))
    return None


class UsageRecorderMiddleware:
    """See the module docstring."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        try:
            self._observe(request, response)
        except Exception:  # recording must never change a response
            logger.debug("Usage recorder skipped a request", exc_info=True)
        return response

    @staticmethod
    def _observe(request, response) -> None:
        if request.method in _SKIPPED_METHODS or response.status_code >= 400:
            return
        match = getattr(request, "resolver_match", None)
        if match is None or not match.url_name:
            return
        recorder.record(actor_for(request), area_for(match.url_name, request.GET), CHANNEL_API)
