"""Cluster 15e — CalculatedModelMixin combinatorial pipeline.

Scenarios 15.16 – 15.18. Verifies calc_and_save_sync wraps every
combinatorial instance, that per-instance row keying is correct, and
that conditional logging within one fan-out produces exactly the
expected subset of CalculationLog rows.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogConditionalChild, LogLoudChild


class TestCluster15e_CombinatorialPipeline(_CalcLogTestCase):
    """Combinatorial expansion + per-instance row keying."""

    def test_15_16_every_instance_wrapped(self):
        """15.16: LogLoudChild.create(unit=['u1','u2','u3']) — all 3
        child rows exist with parent_log = root_row.id. Pins that the
        `with model_logging_context(model):` block at
        CalculatedModelMixin.py:889 runs for EVERY instance, not just
        the first or last.
        """
        root = self._save_root(child_mode="loud", units_csv="u1,u2,u3")
        root_row = self.assert_log_row(root, parent=None)
        for unit in ("u1", "u2", "u3"):
            loud = LogLoudChild.objects.get(unit=unit)
            self.assert_log_row(loud, parent=root_row, contains=f"loud {unit}")
        self.assert_total_rows(4)

    def test_15_17_distinct_per_instance_object_ids(self):
        """15.17: The N child rows have N distinct object_ids, each
        matching a distinct LogLoudChild.pk. No duplicates, no missing.
        """
        self._save_root(child_mode="loud", units_csv="u1,u2,u3")
        from lex.audit_logging.models.CalculationLog import CalculationLog
        child_rows = CalculationLog.objects.filter(
            content_type__model="logloudchild",
        )
        self.assertEqual(child_rows.count(), 3)
        object_ids = sorted(child_rows.values_list("object_id", flat=True))
        expected_pks = sorted(
            LogLoudChild.objects.values_list("pk", flat=True),
        )
        self.assertEqual(
            object_ids, expected_pks,
            f"CalculationLog object_ids ({object_ids}) must match "
            f"LogLoudChild.pks ({expected_pks}) one-to-one.",
        )

    def test_15_18_conditional_logging_within_fanout(self):
        """15.18: PRODUCTION MIRROR — within ONE combinatorial fan-out,
        only the instances whose calculate() actually emits LexLogger
        produce CalculationLog rows.

        LogConditionalChild.calculate() logs iff unit == 'loud_one'.
        With unit=['loud_one','u2','u3'], we get:
          * 3 LogConditionalChild model rows (sanity)
          * 1 root CalculationLog row
          * 1 LogConditionalChild CalculationLog row (the 'loud_one')
          * 0 rows for the other two
        """
        root = self._save_root(
            child_mode="conditional", units_csv="loud_one,u2,u3",
        )
        # Sanity: all 3 instances exist in the DB
        units_created = set(
            LogConditionalChild.objects.values_list("unit", flat=True),
        )
        self.assertEqual(units_created, {"loud_one", "u2", "u3"})

        root_row = self.assert_log_row(root, parent=None)

        loud = LogConditionalChild.objects.get(unit="loud_one")
        self.assert_log_row(loud, parent=root_row, contains="conditional loud_one")

        for unit in ("u2", "u3"):
            quiet = LogConditionalChild.objects.get(unit=unit)
            self.assert_no_log_row(quiet)

        self.assert_total_rows(2)
