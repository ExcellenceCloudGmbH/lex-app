"""
Edge-case coverage for future-valid rows and gaps in valid-time continuity.

Verifies that the bitemporal engine handles out-of-order inserts and
gap scenarios without corrupting the main table.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import models

from lex.core.models.LexModel import LexModel
from lex.core.tests._bitemporal_test_case import DynamicBitemporalModelTestCase


class RobustnessTestModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class BitemporalRobustnessTest(DynamicBitemporalModelTestCase):
    """Edge-case coverage for future-valid rows and gaps in valid-time continuity."""

    model_class = RobustnessTestModel

    def test_future_insert_sync(self):
        """A row that is only valid in the future should not appear in the live main table yet."""
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t_future = t0 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = RobustnessTestModel(name="future_obj")
            obj._history_date = t_future
            obj.save()

        self.assertEqual(
            RobustnessTestModel.objects.count(),
            0,
            "Main table should be empty when the only row is future-valid.",
        )

    def test_gap_in_validity(self):
        """A synchronization during a validity gap should clear the main table."""
        t0 = datetime(2025, 1, 1, 12, 0, 0)
        t1 = t0 + timedelta(minutes=10)
        t2 = t0 + timedelta(minutes=20)
        t_gap = t0 + timedelta(minutes=15)

        with patch("django.utils.timezone.now", return_value=t0):
            obj = RobustnessTestModel.objects.create(name="MsgA")

        original_id = obj.id

        with patch("django.utils.timezone.now", return_value=t1):
            obj.delete()

        with patch("django.utils.timezone.now", return_value=t0):
            replacement = RobustnessTestModel(pk=original_id, name="MsgB")
            replacement._history_date = t2
            replacement.save()

        with patch("django.utils.timezone.now", return_value=t_gap):
            gap_history = self.HistoryModel.objects.filter(name="MsgB").latest("history_id")
            gap_history.save()

        self.assertEqual(RobustnessTestModel.objects.count(), 0)
