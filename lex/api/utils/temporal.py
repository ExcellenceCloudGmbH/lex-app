from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

logger = logging.getLogger(__name__)


def parse_as_of_datetime(raw_value: str | None) -> Optional[datetime]:
    """
    Parse an incoming as_of query value and normalize it to UTC.

    Rules:
    - Offset-aware datetimes are converted to UTC. This is the form clients
      should send: it carries the answer, so the server never has to guess.
    - Naive datetimes are interpreted in the CONFIGURED timezone
      (``timezone.get_current_timezone()``), and log a warning.
    - When USE_TZ is disabled, return a naive UTC datetime for ORM compatibility.

    Why naive input follows the configured zone rather than UTC
    ----------------------------------------------------------
    It previously assumed UTC. That made ``as_of`` the only datetime input in the
    API that did not follow DRF's own convention — ``DateTimeField.enforce_timezone``
    interprets a naive value in ``timezone.get_current_timezone()`` — and it broke
    the feature for every client sending local wall-clock: a Berlin anchor was
    evaluated 1-2h in the future, so stepping back less than that offset never
    reached the pre-edit state (customer report 2026-07-14).

    Aligning the fallback makes a naive anchor correct for the configured zone,
    and the warning makes the remaining guess visible instead of silent — a wrong
    snapshot is worse than an error because it looks plausible.

    The warning is not decoration: it is the signal that a client still needs
    fixing. Once it stops appearing, the naive branch can be replaced with a 400.
    """
    if raw_value is None:
        return None

    raw = str(raw_value).strip()
    if not raw:
        return None

    normalized = raw[:-1] + "+00:00" if raw.endswith("Z") else raw
    parsed = parse_datetime(normalized)
    if parsed is None:
        return None

    if timezone.is_naive(parsed):
        assumed_zone = timezone.get_current_timezone()
        logger.warning(
            "as_of received a naive datetime (%r); interpreting it in the "
            "configured timezone (%s). Clients should send an absolute instant "
            "with an offset or a Z designator so the server does not have to "
            "guess.",
            raw,
            assumed_zone,
        )
        parsed_utc = timezone.make_aware(parsed, assumed_zone).astimezone(dt_timezone.utc)
    else:
        parsed_utc = parsed.astimezone(dt_timezone.utc)

    if settings.USE_TZ:
        return parsed_utc

    return timezone.make_naive(parsed_utc, dt_timezone.utc)
