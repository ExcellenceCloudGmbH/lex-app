"""Cluster 12j — a datetime entered as X reads back as X, for any region.

Intent: under the aware-UTC convention (USE_TZ=True), a client that sends an
explicit instant (the fixed frontend does: ``new Date(pick).toISOString()``)
must get that exact moment back, rendered in each viewer's own zone. This is
the end-to-end round trip that the TIME_ZONE incident broke; it is correct only
when the write path preserves the instant and the read path serves a truthful
offset. Both hold under USE_TZ=True, so these run as live regression gates.

The paired frontend test (``datetimeConventionRoundTrip.test.ts``) proves the
client now emits an explicit instant; here we prove the backend round trip.

Cluster 12j — scenarios 12.46–12.48. Type: E.
Covers: lex/lex_app/settings.py (USE_TZ/TIME_ZONE convention),
        lex/api/serializers/base_serializers.py (DateTimeField round-trip).
Run: python -m lex pytest lex/test_project/tests/serializers/test_12j_datetime_roundtrip_convention.py -v
"""

from __future__ import annotations

import re
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from rest_framework import status

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, WIDE, WideItem

pytestmark = pytest.mark.serializers

BERLIN = ZoneInfo("Europe/Berlin")
_TZ_DESIGNATOR = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def browser_render_in_berlin(served: str) -> datetime:
    """Reproduce ``new Date(served)`` for a viewer in Europe/Berlin."""
    s = str(served)
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(BERLIN)


class TestCluster12j_DatetimeRoundTripConvention(E2ETestCase):
    """Cluster 12j: an explicit instant survives the write→read round trip."""

    e2e_models = ALL_MODELS

    def _post_get(self, instant: str) -> str:
        """POST the explicit instant the fixed frontend sends; GET it back."""
        create = self.client.post(
            self.url_create(WIDE),
            data={"name": f"tz-{instant}", "created_at_ts": instant},
            format="json",
        )
        self.assertIn(
            create.status_code, (200, 201),
            f"Create failed ({create.status_code}): {getattr(create, 'data', None)}",
        )
        item = WideItem.objects.get(name=f"tz-{instant}")
        detail = self.client.get(self.url_detail(WIDE, item.pk))
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        return detail.data["created_at_ts"]

    def test_12_46_summer_instant_round_trips_for_a_berlin_viewer(self) -> None:
        """
        Scenario 12.46: 11:00 Berlin (summer) entered as an instant reads 11:00.
        Given: a Berlin user picks 2026-07-20 11:00; the fixed frontend sends
               the instant "2026-07-20T09:00:00Z" (11:00 +02:00 → UTC)
        When:  the record is POSTed and GET back, then rendered by the browser
        Then:  the Berlin viewer sees 11:00 — the entered wall-clock
        """
        shown = browser_render_in_berlin(self._post_get("2026-07-20T09:00:00Z"))
        self.assertEqual(
            (shown.hour, shown.minute), (11, 0),
            f"Entered 11:00 Berlin, viewer sees {shown:%H:%M} — the instant did "
            f"not survive the round trip.",
        )

    def test_12_47_year_end_midnight_round_trips_across_the_offset(self) -> None:
        """
        Scenario 12.47: Dec 31 00:00 Berlin (winter) reads back as Dec 31 00:00.
        Given: a Berlin user picks 2026-12-31 00:00; the frontend sends the
               instant "2026-12-30T23:00:00Z" (00:00 +01:00 → UTC)
        When:  stored, GET back, rendered by a Berlin (winter) viewer
        Then:  the viewer sees 2026-12-31 00:00 — same date, same time
        """
        shown = browser_render_in_berlin(self._post_get("2026-12-30T23:00:00Z"))
        self.assertEqual(
            (shown.year, shown.month, shown.day, shown.hour, shown.minute),
            (2026, 12, 31, 0, 0),
            f"Year-end midnight came back as {shown:%Y-%m-%d %H:%M} Berlin.",
        )

    def test_12_48_served_value_is_a_truthful_designated_instant(self) -> None:
        """
        Scenario 12.48: the API serves the value with a real tz designator that
        denotes the exact stored instant (no ambiguity, no drift).
        Given: the 09:00Z instant stored above
        When:  read through the API
        Then:  the serialized string carries 'Z'/offset and parses back to 09:00Z
        """
        served = self._post_get("2026-07-20T09:00:00Z")
        self.assertRegex(
            str(served), _TZ_DESIGNATOR,
            f"served {served!r} carries no timezone designator — clients would "
            f"parse it as local time.",
        )
        parsed = browser_render_in_berlin(served).astimezone(ZoneInfo("UTC"))
        self.assertEqual(
            (parsed.hour, parsed.minute), (9, 0),
            f"served {served!r} does not denote the stored 09:00Z instant.",
        )
