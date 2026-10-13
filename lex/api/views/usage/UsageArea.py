"""``POST api/usage/area``: the web interface says a person used a product area today.

Fleet metrics, phase 1, K2 feature reach (spec §5.3.2). The interface calls this once per
browser tab, product area and UTC day. The body names an area and whether the interface
ran embedded, and nothing else:

* the person is the session's user, never a field of the body;
* the day is the server's UTC date when the event arrives;
* the row goes through the recorder's insert-once path, on channel ``ui``, or ``ui_embed``
  when ``embed`` is true.

Session-only, with DRF's CSRF check in force, because only the interface sends these
events. An anonymous caller is refused by the login middleware (401). An API key has no
session user and is refused here (403), so it never appears on an interface channel.

Answers:

* 204 — recorded, or deliberately not: ``LEX_USAGE_METRICS=false``, a non-PostgreSQL
  database, or a recorder paused after a database error. The interface cannot tell these
  apart and has no reason to.
* 400 — the body is not exactly ``{"area": <one of UI_AREAS>, "embed": <bool>}``. Extra
  keys are refused as well: an event carries nothing else (the privacy boundary).

Design: docs/superpowers/specs/2026-09-29-fleet-metrics-phase1-design.md (§5.3.2)
"""

from __future__ import annotations

from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lex.core.services.usage_areas import UI_AREAS
from lex.core.services.usage_metrics import CHANNEL_UI, CHANNEL_UI_EMBED, PERSON, recorder

BODY_KEYS = frozenset({"area", "embed"})


def _bad_request(detail: str) -> Response:
    return Response({"detail": detail}, status=status.HTTP_400_BAD_REQUEST)


class UsageAreaView(APIView):
    """See the module docstring."""

    http_method_names = ["post"]
    authentication_classes = [SessionAuthentication]
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser]

    def post(self, request, *args, **kwargs):
        body = request.data
        if not isinstance(body, dict) or set(body) != BODY_KEYS:
            return _bad_request('The body must be exactly {"area": <area>, "embed": <bool>}.')
        area, embed = body["area"], body["embed"]
        if not isinstance(area, str) or area not in UI_AREAS:
            return _bad_request("Unknown product area.")
        if not isinstance(embed, bool):
            return _bad_request('"embed" must be true or false.')
        recorder.record(
            (PERSON, str(request.user.pk)),
            area,
            CHANNEL_UI_EMBED if embed else CHANNEL_UI,
        )
        return Response(status=status.HTTP_204_NO_CONTENT)
