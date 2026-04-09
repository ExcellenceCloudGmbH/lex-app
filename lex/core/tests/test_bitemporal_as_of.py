"""
Tests for valid-time and system-time as-of queries.

Verifies that ``get_queryset_as_of`` correctly hides future-valid rows
and respects both valid-time and system-time dimensions.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import models

from lex.core.models.LexModel import LexModel
from lex.core.services.Bitemporal import get_queryset_as_of
from lex.core.tests._bitemporal_test_case import DynamicBitemporalModelTestCase

class AsOfTestModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

class BitemporalAsOfTest(DynamicBitemporalModelTestCase):
    """Tests for valid-time and system-time as-of queries."""

    model_class = AsOfTestModel

    def test_as_of_validity(self):
        """Future-valid rows stay hidden until the requested valid-time instant."""
        T0 = datetime(2025, 1, 1, 12, 0, 0)
        T_Future = T0 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T0):
            future_obj = AsOfTestModel(name="FutureVal")
            future_obj._history_date = T_Future
            future_obj.save()

        with patch("django.utils.timezone.now", return_value=T0):
            self.assertEqual(AsOfTestModel.objects.count(), 0)

            qs_now = get_queryset_as_of(AsOfTestModel, T0)
            self.assertEqual(qs_now.count(), 0, "as_of=Now should exclude future rows")

            qs_future = get_queryset_as_of(AsOfTestModel, T_Future)
            self.assertEqual(qs_future.count(), 1, "as_of=Future should show the row")
            self.assertEqual(qs_future.first().name, "FutureVal")

    def test_as_of_historical(self):
        """As-of on the main model should return the row valid at that moment."""
        T0 = datetime(2025, 1, 1, 10, 0, 0)
        T1 = T0 + timedelta(hours=1)
        T2 = T0 + timedelta(hours=2)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = AsOfTestModel(name="OldVal")
            obj._history_date = T0
            obj.save()

        with patch("django.utils.timezone.now", return_value=T1):
            obj.name = "NewVal"
            obj._history_date = T1
            obj.save()

        with patch("django.utils.timezone.now", return_value=T2):
            qs_past = get_queryset_as_of(AsOfTestModel, T0 + timedelta(minutes=30))
            self.assertEqual(qs_past.first().name, "OldVal")

            qs_current = get_queryset_as_of(AsOfTestModel, T1 + timedelta(minutes=30))
            self.assertEqual(qs_current.first().name, "NewVal")

    def test_as_of_with_history_model_class(self):
        """Passing the history model should query system-time knowledge via meta-history."""
        T0 = datetime(2025, 1, 1, 10, 0, 0)

        with patch("django.utils.timezone.now", return_value=T0):
            obj = AsOfTestModel.objects.create(name="Original")

        T1 = T0 + timedelta(hours=1)
        with patch("django.utils.timezone.now", return_value=T1):
            obj.name = "Changed"
            obj.save()

        qs_valid = get_queryset_as_of(AsOfTestModel, T1)
        self.assertEqual(qs_valid.first().name, "Changed")

        qs_sys_t0 = get_queryset_as_of(self.HistoryModel, T0)
        self.assertEqual(qs_sys_t0.count(), 1)
        self.assertTrue(hasattr(qs_sys_t0.first(), "meta_history_id"), "Should return MetaHistory")
        self.assertEqual(qs_sys_t0.first().name, "Original")

        qs_sys_t1 = get_queryset_as_of(self.HistoryModel, T1 + timedelta(minutes=1))
        self.assertEqual(qs_sys_t1.count(), 2)
