from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone
from typing import Optional

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import ValidationError

logger = logging.getLogger(__name__)


def parse_as_of_datetime(raw_value: str | None) -> Optional[datetime]:
    """
    Parse an incoming as_of query value and normalize it to UTC.

    Rules:
    - Offset-aware datetimes are converted to UTC. This is the only accepted
      form: it carries its own zone, so the server never has to guess.
    - Naive datetimes are REJECTED with a 400 (``ValidationError``).
    - When USE_TZ is disabled, return a naive UTC datetime for ORM compatibility.

    Why a naive anchor is rejected rather than assumed
    -------------------------------------------------
    Every guess here is a silent wrong answer. A naive anchor originally assumed
    UTC, which put a Berlin client's wall clock 1-2h in the future: stepping back
    less than that offset never reached the pre-edit state, and the endpoint
    returned a plausible snapshot of the wrong version (customer report
    2026-07-14). The previous commit made the guess follow the configured
    timezone and logged it, which was correct for the common case but still a
    guess — wrong for any caller in another zone.

    Now that every first-party client sends an absolute instant
    (process-admin-general-client ``toAsOfIsoString``), a naive anchor is a bug
    rather than a use case, and failing loudly makes it immediately visible
    instead of quietly time-travelling to the wrong instant. This is only
    tenable because the callers are first-party; it is a deliberate contract
    narrowing, not a defensive default.
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
        # Logged as well as raised: the 400 tells the caller, the log tells us
        # which client still needs fixing.
        logger.warning(
            "as_of rejected a naive datetime (%r): no timezone attached, so the "
            "instant it denotes is ambiguous.",
            raw,
        )
        raise ValidationError(
            {
                "as_of": (
                    "must carry a timezone — send an absolute instant such as "
                    "2026-07-15T12:30:00Z or 2026-07-15T14:30:00+02:00. A value "
                    "without an offset is ambiguous and would silently select "
                    "the wrong snapshot."
                )
            }
        )

    parsed_utc = parsed.astimezone(dt_timezone.utc)

    if settings.USE_TZ:
        return parsed_utc

    return timezone.make_naive(parsed_utc, dt_timezone.utc)
