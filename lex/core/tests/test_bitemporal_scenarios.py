"""
Regression tests for representative bitemporal lifecycle scenarios.

Covers multi-step create/update/correction chains and validates that
valid-time boundaries stay consistent after each mutation.
"""

from datetime import datetime, timedelta
from unittest.mock import patch

from django.db import models

from lex.core.models.LexModel import LexModel
from lex.core.tests._bitemporal_test_case import DynamicBitemporalModelTestCase

class ScenarioTestModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"

class BitemporalScenarioTest(DynamicBitemporalModelTestCase):
    """Regression tests for representative bitemporal lifecycle scenarios."""

    model_class = ScenarioTestModel

    def test_user_scenario(self):
        """A correction that moves a future row later should re-close its predecessor."""
        T12_00 = datetime(2024, 1, 1, 12, 0, 0)
        T12_05 = T12_00 + timedelta(minutes=5)
        T12_08 = T12_00 + timedelta(minutes=8)
        T13_00 = T12_00 + timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="melih")

        with patch("django.utils.timezone.now", return_value=T12_05):
            obj.name = "melih2"
            obj.save()

        with patch("django.utils.timezone.now", return_value=T12_08):
            h_melih2 = self.HistoryModel.objects.get(name="melih2")
            h_melih2.valid_from = T13_00
            h_melih2.save()

        h_rows = list(self.HistoryModel.objects.all().order_by("valid_from"))
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(h_rows[0].name, "melih")
        self.assertEqual(h_rows[0].valid_from, T12_00)
        self.assertEqual(h_rows[0].valid_to, T13_00)
        self.assertEqual(h_rows[1].name, "melih2")
        self.assertEqual(h_rows[1].valid_from, T13_00)
        self.assertIsNone(h_rows[1].valid_to)

        m_melih_closed = self.MetaModel.objects.filter(
            name="melih", valid_to__isnull=False
        ).latest("sys_from")
        self.assertEqual(m_melih_closed.valid_to, T13_00)
        self.assertEqual(m_melih_closed.sys_from, T12_05)
        self.assertIsNone(m_melih_closed.sys_to)

        current_obj = ScenarioTestModel.objects.get(pk=obj.pk)
        self.assertEqual(current_obj.name, "melih")

        T12_10 = T12_00 + timedelta(minutes=10)
        T12_30 = T12_00 + timedelta(minutes=30)

        with patch("django.utils.timezone.now", return_value=T12_10):
            h_melih2.valid_from = T12_30
            h_melih2.save()

        m_melih_closed.refresh_from_db()
        self.assertEqual(m_melih_closed.valid_to, T12_30)
        self.assertEqual(m_melih_closed.sys_from, T12_05)

    def test_deletion(self):
        """Deleting the live row should close the previously valid history interval."""
        T12_00 = datetime(2024, 1, 1, 12, 0, 0)
        T12_10 = T12_00 + timedelta(minutes=10)

        with patch("django.utils.timezone.now", return_value=T12_00):
            obj = ScenarioTestModel.objects.create(name="to_be_deleted")

        self.assertEqual(ScenarioTestModel.objects.count(), 1)

        with patch("django.utils.timezone.now", return_value=T12_10):
            obj.delete()

        self.assertEqual(ScenarioTestModel.objects.count(), 0)

        h_rows = list(self.HistoryModel.objects.all().order_by("history_id"))
        self.assertEqual(len(h_rows), 2)
        self.assertEqual(h_rows[1].history_type, "-")
        self.assertEqual(h_rows[1].valid_from, T12_10)
        self.assertEqual(h_rows[0].valid_to, T12_10)

    def test_retroactive_creation(self):
        """A retroactive `_history_date` should affect valid time, not system time."""
        T12_00 = datetime(2024, 1, 1, 12, 0, 0)
        T11_00 = T12_00 - timedelta(hours=1)

        with patch("django.utils.timezone.now", return_value=T12_00):
            retro_obj = ScenarioTestModel(name="retro")
            retro_obj._history_date = T11_00
            retro_obj.save()

        h1 = self.HistoryModel.objects.first()
        self.assertEqual(h1.valid_from, T11_00)
        self.assertIsNone(h1.valid_to)

        m1 = self.MetaModel.objects.first()
        self.assertEqual(m1.sys_from, T12_00)
        self.assertIsNone(m1.sys_to)


