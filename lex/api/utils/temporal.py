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

    return timezone.make_naive(parsed_utc, dt_timezone.utc)
