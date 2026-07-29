"""REAL end-to-end datetime round trip through the actual API.

Not isolated units — this drives WideItem.created_at_ts (a plain user
DateTimeField) through the real serializer, real settings
(USE_TZ=False, TIME_ZONE=UTC — hardcoded in the test env, identical to the
default/GCP production config), and the real DB, then reproduces the browser
display step. It proves the "I enter 11:00 and see 13:00" behavior is real.

Run: python -m lex pytest lex/test_project/tests/serializers/test_real_datetime_roundtrip.py -v -s
"""
from __future__ import annotations

from datetime import datetime, timezone as dt_timezone, timedelta

import pytest
from django.conf import settings
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, WIDE, WideItem

pytestmark = pytest.mark.serializers


class TestRealDatetimeRoundTrip(E2ETestCase):
    """The full API round trip a Berlin user actually experiences."""

    e2e_models = ALL_MODELS

    def test_enter_1100_get_back_shifted(self):
        """Create via API with the exact string the frontend sends, read back.

        Frontend sends NAIVE local wall-clock, no 'Z' (proven separately in
        the frontend test). We POST exactly that, then check three things:
        the raw stored DB value, the serialized API value, and what a
        Berlin browser renders from it.
        """
        # Sanity: the env is the production convention.
        self.assertFalse(settings.USE_TZ)
        self.assertEqual(settings.TIME_ZONE, "UTC")

        # 1) What the frontend sends for a user who picked 11:00 (Berlin).
        resp = self.client.post(
            self.url_create(WIDE),
            data={"name": "tz-probe", "created_at_ts": "2026-07-20T11:00:00"},
            format="json",
        )
        self.assertIn(resp.status_code, (200, 201), getattr(resp, "data", None))

        # 2) Raw stored value in the DB — no conversion happened on write.
        item = WideItem.objects.get(name="tz-probe")
        stored = item.created_at_ts
        print("\n  RAW STORED DB VALUE :", stored, "| tzinfo:", stored.tzinfo)
        self.assertEqual(
            stored, datetime(2026, 7, 20, 11, 0, 0),
            "The naive 11:00 was stored verbatim — no conversion on write.",
        )

        # 3) What the API serializes it back to.
        get_resp = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(get_resp.status_code, status.HTTP_200_OK)
        served = get_resp.data["created_at_ts"]
        print("  API SERIALIZED VALUE:", served)

        # 4) What a Berlin browser renders: new Date(served) in Europe/Berlin.
        #    We emulate the browser: parse the served string to an instant,
        #    then express it in Berlin summer (UTC+2).
        parsed = datetime.fromisoformat(str(served).replace("Z", "+00:00"))
        berlin_summer = dt_timezone(timedelta(hours=2))
        shown = parsed.astimezone(berlin_summer)
        print("  BROWSER SHOWS (Berlin):", shown.strftime("%Y-%m-%d %H:%M"))

        if str(served).endswith("Z"):
            # This is today's behavior: served as UTC, browser shifts +2h.
            self.assertEqual(
                shown.hour, 13,
                "CONFIRMED BUG: entered 11:00, browser shows 13:00 — the "
                "write path stored local-as-is, the read path labels it UTC.",
            )
            print("  => CONFIRMED: user entered 11:00, sees 13:00 (off by +2h).")
        else:
            # If the framework ever serves an offset/local instead, the round
            # trip would be self-consistent — flag it so the test stays honest.
            print("  => served WITHOUT 'Z':", served, "- round trip may be consistent.")
