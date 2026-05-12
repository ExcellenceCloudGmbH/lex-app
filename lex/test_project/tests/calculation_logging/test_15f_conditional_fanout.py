"""
Cluster 15f — conditional fan-out (15.14–15.15).

``LogConditionalChild`` only calls ``LexLogger`` when ``unit ==
"loud_one"``. With both ``loud_one`` and ``quiet_one`` produced by the
same combinatoric fan-out, the loud one writes a row and the quiet one
does not — exercising the production case where some siblings under
the same root log and some don't.
"""
from __future__ import annotations

from . import _CalcLogTestCase
from .models import LogConditionalChild, LogRootCalc


class TestCluster15f_ConditionalFanout(_CalcLogTestCase):
    """Per-instance log/no-log within one combinatoric expansion."""

    # -- 15.14 ---------------------------------------------------------
    def test_15_14_conditional_loud_one_writes_row(self):
        """Single-unit fan-out (``units_csv="loud_one"``) writes the
        loud-one row parented to the root.
        """
        root = LogRootCalc(
            name="r14", child_mode="conditional", units_csv="loud_one",
        )
        self.drive_root(root)

        root_row = self.assert_log_row(root, parent=None)
        loud_one = LogConditionalChild.objects.get(unit="loud_one")
        self.assert_log_row(
            loud_one, parent=root_row, contains="conditional loud_one",
        )
        self.assert_total_rows(2)

    # -- 15.15 ---------------------------------------------------------
    def test_15_15_conditional_some_log_some_dont(self):
        """Two-unit fan-out (``loud_one,quiet_one``) — both instances
        exist post-calc, but only ``loud_one`` writes a CalculationLog
        row. Total = root + loud_one = 2.
        """
        root = LogRootCalc(
            name="r15",
            child_mode="conditional",
            units_csv="loud_one,quiet_one",
        )
        self.drive_root(root)

        # Both instances must exist (calculate ran on both).
        self.assertEqual(
            LogConditionalChild.objects.count(), 2,
            "Both combinatoric units should have been materialised.",
        )

        root_row = self.assert_log_row(root, parent=None)
        loud_one = LogConditionalChild.objects.get(unit="loud_one")
        quiet_one = LogConditionalChild.objects.get(unit="quiet_one")
        self.assert_log_row(loud_one, parent=root_row)
        self.assert_no_log_row(quiet_one)
        self.assert_total_rows(2)

