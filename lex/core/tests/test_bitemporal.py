"""
Core regression coverage for strict bitemporal chaining behaviour.

Verifies that correcting a history row rewires valid-time boundaries and
preserves the system-time meta-history audit trail.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import models

from lex.core.models.LexModel import LexModel
from lex.core.services.Bitemporal import get_queryset_as_of
from lex.core.tests._bitemporal_test_case import DynamicBitemporalModelTestCase


class TestBitemporalModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class BitemporalLogicTest(DynamicBitemporalModelTestCase):
    """Core regression coverage for strict bitemporal chaining behavior."""

    model_class = TestBitemporalModel

    def test_scenario_three_tables(self):
        """A corrected history row should rewire valid-time and preserve system-time history."""
        base_time = datetime(2024, 4, 1, 12, 0, 0)

        with patch("django.utils.timezone.now", return_value=base_time):
            obj = TestBitemporalModel.objects.create(name="melih")

        h1 = obj.history.first()
        self.assertEqual(h1.name, "melih")
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(h1.valid_to)

        m1 = h1.meta_history.first()
        self.assertEqual(m1.history_object, h1)
        self.assertTrue(abs((m1.sys_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(m1.sys_to)

        t1 = base_time + timedelta(minutes=5)
        with patch("django.utils.timezone.now", return_value=t1):
            obj.name = "melih2"
            obj.save()

        h1.refresh_from_db()
        h2 = obj.history.latest("valid_from")
        self.assertEqual(h2.name, "melih2")
        self.assertTrue(abs((h1.valid_to - t1).total_seconds()) < 1.0)
        self.assertIsNone(h2.valid_to)

        t_correction = base_time - timedelta(hours=1)
        t2 = base_time + timedelta(minutes=8)
        with patch("django.utils.timezone.now", return_value=t2):
            h2.valid_from = t_correction
            h2.save()

        h1.refresh_from_db()
        h2.refresh_from_db()
        self.assertTrue(abs((h2.valid_from - t_correction).total_seconds()) < 1.0)
        self.assertTrue(abs((h2.valid_to - base_time).total_seconds()) < 1.0)
        self.assertTrue(abs((h1.valid_from - base_time).total_seconds()) < 1.0)
        self.assertIsNone(h1.valid_to)

        qs_initial = get_queryset_as_of(TestBitemporalModel, base_time + timedelta(minutes=1))
        self.assertEqual(qs_initial.count(), 1)
        self.assertEqual(qs_initial.first().name, "melih")

        qs_after_update = get_queryset_as_of(TestBitemporalModel, t1 + timedelta(minutes=1))
        rows_after_update = list(qs_after_update)
        self.assertEqual(len(rows_after_update), 1)
        self.assertEqual(rows_after_update[0].name, "melih")

        qs_system_before_correction = get_queryset_as_of(
            TestBitemporalModel.history.model,
            t1 + timedelta(minutes=1),
        )
        self.assertGreaterEqual(qs_system_before_correction.count(), 1)
        self.assertIn(
            "melih2",
            [meta_row.name for meta_row in qs_system_before_correction],
        )

        qs_after_correction = get_queryset_as_of(TestBitemporalModel, t2 + timedelta(minutes=1))
        self.assertEqual(qs_after_correction.count(), 1)
        self.assertEqual(qs_after_correction.first().name, "melih")

        qs_system_after_correction = get_queryset_as_of(
            TestBitemporalModel.history.model,
            t2 + timedelta(minutes=1),
        )
        self.assertGreaterEqual(qs_system_after_correction.count(), 2)
        self.assertIn(
            "melih2",
            [meta_row.name for meta_row in qs_system_after_correction],
        )
        corrected_meta = next(
            meta_row
            for meta_row in qs_system_after_correction
            if meta_row.name == "melih2"
        )
        self.assertTrue(
            abs((corrected_meta.valid_from - t_correction).total_seconds()) < 1.0
        )

