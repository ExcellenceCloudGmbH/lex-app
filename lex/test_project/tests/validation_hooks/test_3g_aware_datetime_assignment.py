"""Cluster 3g — a DateTimeField becomes aware the moment it is assigned.

Intent: under USE_TZ=True Django only normalizes datetimes at the DB boundary,
so a value assigned in memory (fixture load, Excel parse, ``obj.happened_at =
datetime.now()``) stays naive until the next save/fetch round trip while a
fetched value is aware. Mixing the two in downstream code (e.g. one pandas
column fed from both a queryset and a freshly-built instance) raises
``TypeError: Cannot compare tz-naive and tz-aware timestamps`` mid-calculation.
``AwareDateTimeDescriptor`` closes the gap: every ``DateTimeField`` on a
``LexModel`` subclass is normalized to the default timezone at assignment —
the same interpretation Django itself applies at save time, so the stored
instant never changes; the in-memory object simply agrees with its own future
save and with what a re-fetch returns. A regression here silently reopens the
naive/aware seam in every downstream project.

Cluster 3g — scenarios 3.33–3.38. Type: I.
Covers: lex/core/models/LexModel.py
        (AwareDateTimeDescriptor, _install_aware_datetime_descriptors).
Run: python -m lex pytest lex/test_project/tests/validation_hooks/test_3g_aware_datetime_assignment.py -v
"""

from __future__ import annotations

from datetime import date, datetime, timezone as dt_timezone

import pytest
from django.conf import settings
from django.utils import timezone

from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import ALL_MODELS, StampedItem

pytestmark = pytest.mark.validation_hooks

SUMMER_NAIVE = datetime(2026, 7, 20, 11, 0, 0)   # Berlin: +02:00 → 09:00Z
WINTER_NAIVE = datetime(2026, 12, 31, 0, 0, 0)   # Berlin: +01:00 → Dec 30 23:00Z


class TestCluster03g_AwareDatetimeAssignment(E2ETestCase):
    """Cluster 3g: DateTimeField assignments are aware from the first moment."""

    e2e_models = ALL_MODELS

    def test_3_33_naive_constructor_kwarg_becomes_aware(self) -> None:
        """
        Scenario 3.33: a naive datetime passed to the model constructor is
        aware immediately, interpreted in the default timezone.
        Given: a naive wall-clock value
        When: passed as a constructor kwarg (no save, no fetch)
        Then: the attribute is aware and denotes the same instant Django
              would persist for it (make_aware in the default timezone)
        """
        item = StampedItem(name="ctor", happened_at=SUMMER_NAIVE)
        self.assertIsNotNone(
            item.happened_at.tzinfo,
            "a naive constructor kwarg must be aware on the instance — the "
            "naive/aware mixing window this descriptor exists to close.",
        )
        expected = timezone.make_aware(SUMMER_NAIVE, timezone.get_default_timezone())
        self.assertEqual(
            item.happened_at, expected,
            f"assignment must use the save-time interpretation (default tz); "
            f"got {item.happened_at}, expected {expected}.",
        )

    def test_3_34_dst_offsets_are_respected_on_attribute_set(self) -> None:
        """
        Scenario 3.34: attribute assignment picks the DST-correct offset —
        summer +02:00, winter +01:00 on a Berlin instance.
        Given: naive summer and winter wall-clock values
        When: assigned to the attribute after construction
        Then: each carries its own seasonal UTC offset
        """
        if settings.TIME_ZONE != "Europe/Berlin":
            self.skipTest(f"default zone is {settings.TIME_ZONE}, not Europe/Berlin")
        item = StampedItem(name="dst")
        item.happened_at = SUMMER_NAIVE
        self.assertEqual(
            item.happened_at.utcoffset().total_seconds(), 2 * 3600,
            f"summer wall-clock must carry +02:00, got {item.happened_at}.",
        )
        item.happened_at = WINTER_NAIVE
        self.assertEqual(
            item.happened_at.utcoffset().total_seconds(), 1 * 3600,
            f"winter wall-clock must carry +01:00, got {item.happened_at}.",
        )

    def test_3_35_aware_values_pass_through_untouched(self) -> None:
        """
        Scenario 3.35: an already-aware value is not converted — tzinfo and
        instant are preserved exactly as assigned.
        Given: an aware UTC datetime
        When: assigned
        Then: same instant, still UTC (no silent re-zoning)
        """
        aware_utc = datetime(2026, 7, 20, 9, 0, 0, tzinfo=dt_timezone.utc)
        item = StampedItem(name="aware", happened_at=aware_utc)
        self.assertEqual(
            item.happened_at, aware_utc,
            "an aware assignment must keep its instant.",
        )
        self.assertEqual(
            item.happened_at.utcoffset(), aware_utc.utcoffset(),
            f"an aware assignment must keep its own zone (no conversion); "
            f"got {item.happened_at}.",
        )

    def test_3_36_non_datetime_values_pass_through(self) -> None:
        """
        Scenario 3.36: values that are not datetimes are left alone — None,
        a plain date, and a string reach the field untouched (their handling
        stays with Django's own to_python/validation, not the descriptor).
        Given: None, date(...), and an ISO string
        When: assigned
        Then: each is stored on the instance exactly as given
        """
        item = StampedItem(name="passthrough")
        item.happened_at = None
        self.assertIsNone(item.happened_at, "None must remain None.")
        d = date(2026, 7, 20)
        item.happened_at = d
        self.assertIs(
            item.happened_at, d,
            "a plain date is not a datetime and must pass through untouched.",
        )
        s = "2026-07-20T11:00:00"
        item.happened_at = s
        self.assertIs(
            item.happened_at, s,
            "a string must pass through — parsing stays Django's job.",
        )

    def test_3_37_save_then_refetch_preserves_the_instant(self) -> None:
        """
        Scenario 3.37: the descriptor changes WHEN a value becomes aware,
        never WHAT is stored — a naive assignment saves and refetches as the
        exact instant Django would have persisted anyway.
        Given: a naive wall-clock value assigned and saved
        When: the row is re-fetched from the DB
        Then: fetched == make_aware(naive, default tz) — instant unchanged
        """
        StampedItem.objects.create(name="roundtrip", happened_at=SUMMER_NAIVE)
        fetched = StampedItem.objects.get(name="roundtrip").happened_at
        expected = timezone.make_aware(SUMMER_NAIVE, timezone.get_default_timezone())
        self.assertEqual(
            fetched, expected,
            f"DB round trip must preserve the instant; got {fetched}, "
            f"expected {expected}.",
        )
        self.assertIsNotNone(fetched.tzinfo, "fetched value must be aware.")

    def test_3_38_deferred_field_still_lazy_loads_aware(self) -> None:
        """
        Scenario 3.38: turning the field descriptor into a data descriptor
        must not break deferred loading — a field excluded via .only() is
        still absent until touched, then loads as the aware stored instant.
        Given: a saved row fetched with .only('id', 'name')
        When: the deferred happened_at attribute is accessed
        Then: it lazy-loads from the DB, aware, equal to the stored instant
        """
        created = StampedItem.objects.create(name="deferred", happened_at=SUMMER_NAIVE)
        lean = StampedItem.objects.only("id", "name").get(pk=created.pk)
        self.assertNotIn(
            "happened_at", lean.__dict__,
            ".only() must still defer the field (data-descriptor regression).",
        )
        value = lean.happened_at  # triggers the deferred fetch
        expected = timezone.make_aware(SUMMER_NAIVE, timezone.get_default_timezone())
        self.assertEqual(
            value, expected,
            f"deferred access must return the aware stored instant; got {value}.",
        )
        self.assertIsNotNone(value.tzinfo, "deferred load must return aware.")
