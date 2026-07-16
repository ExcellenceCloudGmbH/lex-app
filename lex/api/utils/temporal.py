from __future__ import annotations

from datetime import datetime, timezone as dt_timezone
from typing import Optional

from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


def parse_as_of_datetime(raw_value: str | None) -> Optional[datetime]:
    """
    Parse an incoming as_of query value and normalize it to UTC.

    Rules:
    - Naive datetimes are interpreted as UTC.
    - Offset-aware datetimes are converted to UTC.
    - When USE_TZ is disabled, return a naive UTC datetime for ORM compatibility.
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
        parsed_utc = timezone.make_aware(parsed, dt_timezone.utc)
    else:
        parsed_utc = parsed.astimezone(dt_timezone.utc)

    if settings.USE_TZ:
        return parsed_utc

    # Naive storage: the ORM compares against naive values whose meaning is
    # the TIME_ZONE convention (UTC by default; Europe/Berlin on instances
    # that restored the pre-rc212 local convention via LEX_TIME_ZONE). The
    # parsed instant must be expressed in that same convention or every
    # comparison shifts by the UTC offset.
    return timezone.make_naive(parsed_utc, timezone.get_default_timezone())
