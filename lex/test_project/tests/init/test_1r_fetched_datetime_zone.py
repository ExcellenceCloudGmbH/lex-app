"""Cluster 1r — fetched datetimes come back in the DB-session display zone.

Intent: with the PostgreSQL connection session set to ``TIME_ZONE`` (Berlin) via
``DATABASES['default']['TIME_ZONE']``, a fetched ``DateTimeField`` is returned as
an aware datetime in the display zone rather than UTC — so ``str()`` / ``.date()``
/ ``.hour`` and model ``__str__`` render local with NO per-field or per-model
changes, while the stored instant is unchanged. This guards the settings wiring
against a silent revert to UTC reads (which is what shows the confusing UTC
wall-clock in labels/exports).

Cluster 1r — scenarios 1.201–1.202. Type: I.
Covers: lex/lex_app/settings.py (DATABASES['default']['TIME_ZONE'] = TIME_ZONE).
Run: python -m lex pytest lex/test_project/tests/init/test_1r_fetched_datetime_zone.py -v
"""

from __future__ import annotations

from datetime import datetime, timezone as dt_timezone

import pytest
from django.conf import settings
from django.utils import timezone

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, IncidentDatetimeItem

pytestmark = pytest.mark.init

INSTANT = datetime(2026, 7, 20, 9, 0, 0, tzinfo=dt_timezone.utc)  # 09:00Z = 11:00 Berlin (summer)


class TestCluster01r_FetchedDatetimeZone(E2ETestCase):
    """Cluster 1r: reads return datetimes in the configured display zone."""

    e2e_models = ALL_MODELS

    def test_1_201_fetched_datetime_is_in_the_display_zone(self) -> None:
        """
        Scenario 1.201: a fetched DateTimeField is aware in TIME_ZONE, not UTC,
        and preserves the stored instant.
        Given: a row whose event_at is the instant 09:00Z
        When: the row is re-fetched from the DB
        Then: the value carries the TIME_ZONE offset for that date (not +00:00),
              and still equals the original 09:00Z instant (storage unchanged)
        """
        IncidentDatetimeItem.objects.create(name="tz-read", event_at=INSTANT)
        fetched = IncidentDatetimeItem.objects.get(name="tz-read").event_at

        # the stored instant is the same absolute moment — nothing corrupted
        self.assertEqual(
            fetched, INSTANT,
            f"the stored instant must be unchanged; got {fetched}.",
        )
        # returned in the display zone: its offset matches localtime's for that
        # instant (== +00:00 only if TIME_ZONE is itself UTC)
        self.assertEqual(
            fetched.utcoffset(), timezone.localtime(INSTANT).utcoffset(),
            f"a fetched datetime should carry the {settings.TIME_ZONE} offset, "
            f"got {fetched.utcoffset()} (str={fetched}).",
        )

    def test_1_202_berlin_wall_clock_reads_local_without_localtime(self) -> None:
        """
        Scenario 1.202: on a Berlin instance the fetched value's wall-clock reads
        Berlin directly (str/.hour), with no timezone.localtime() call.
        Given: the same 09:00Z instant, TIME_ZONE=Europe/Berlin
        When: re-fetched
        Then: .hour/.minute read 11:00 (summer) — the local wall-clock
        """
        if settings.TIME_ZONE != "Europe/Berlin":
            self.skipTest(f"display zone is {settings.TIME_ZONE}, not Europe/Berlin")
        IncidentDatetimeItem.objects.create(name="tz-berlin", event_at=INSTANT)
        fetched = IncidentDatetimeItem.objects.get(name="tz-berlin").event_at
        self.assertEqual(
            (fetched.hour, fetched.minute), (11, 0),
            f"fetched value should read 11:00 Berlin wall-clock, got {fetched}.",
        )
