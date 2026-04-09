"""
Regression coverage for the canonical user-provided bitemporal trace.

Proves that retroactive corrections extend predecessor valid-time
boundaries correctly.
"""

from datetime import datetime
from unittest.mock import patch

from django.db import models

from lex.core.models.LexModel import LexModel
from lex.core.tests._bitemporal_test_case import DynamicBitemporalModelTestCase


class TraceModel(LexModel):
    name = models.CharField(max_length=100)

    class Meta:
        app_label = "lex_app"


class BitemporalTraceTest(DynamicBitemporalModelTestCase):
    """Regression coverage for the canonical user-provided bitemporal trace."""

    model_class = TraceModel

    def test_trace_scenario(self):
        """Correcting a later row to start at 13:00 should extend the previous row to 13:00."""
        t_1200 = datetime(2026, 1, 26, 12, 0, 0)
        t_1205 = datetime(2026, 1, 26, 12, 5, 0)
        t_1208 = datetime(2026, 1, 26, 12, 8, 0)
        t_1300 = datetime(2026, 1, 26, 13, 0, 0)

        with patch("django.utils.timezone.now", return_value=t_1200):
            obj = TraceModel.objects.create(name="melih")

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 1)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_from, t_1200)
        self.assertIsNone(h_objs[0].valid_to)

        with patch("django.utils.timezone.now", return_value=t_1205):
            obj.name = "melih2"
            obj.save()

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 2)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1205)
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1205)
        self.assertIsNone(h_objs[1].valid_to)

        with patch("django.utils.timezone.now", return_value=t_1208):
            h_melih2 = h_objs[1]
            h_melih2.valid_from = t_1300
            h_melih2.save()

        h_objs = list(self.HistoryModel.objects.filter(id=obj.id).order_by("valid_from"))
        self.assertEqual(len(h_objs), 2)
        self.assertEqual(h_objs[0].name, "melih")
        self.assertEqual(h_objs[0].valid_to, t_1300)
        self.assertEqual(h_objs[1].name, "melih2")
        self.assertEqual(h_objs[1].valid_from, t_1300)
        self.assertIsNone(h_objs[1].valid_to)

        meta_objs = list(self.MetaModel.objects.order_by("sys_from", "id"))
        self.assertGreaterEqual(len(meta_objs), 2)
        self.assertTrue(any(meta_row.name == "melih" for meta_row in meta_objs))
        self.assertTrue(any(meta_row.name == "melih2" for meta_row in meta_objs))
