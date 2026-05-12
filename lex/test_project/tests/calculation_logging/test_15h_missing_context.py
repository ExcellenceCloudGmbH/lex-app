"""
Cluster 15h — graceful failure when calculation context is missing (15.18).

Pins the "logging never crashes the calling thread" contract:
``LexLogger().log()`` invoked outside any operation_context /
model_logging_context (e.g. from a Celery worker that lost its context
propagation) must swallow the ``ContextResolutionError`` and write
**zero** ``CalculationLog`` rows — not raise into the caller.
"""
from __future__ import annotations

from lex.api.utils import operation_context
from lex.audit_logging.handlers.LexLogger import LexLogger
from lex.audit_logging.models.CalculationLog import CalculationLog

from . import _CalcLogTestCase


class TestCluster15h_MissingContext(_CalcLogTestCase):
    """LexLogger.log() with no calc context: silent skip, no row, no raise."""

    def setUp(self):
        super().setUp()
        # Strip the calculation_id seeded by the base setUp so that
        # ContextResolver.resolve() raises ContextResolutionError.
        operation_context.set({})
        # Also clear the rows the base setUp may have left behind for
        # this calc_id (none expected, but be explicit).
        CalculationLog.objects.all().delete()

    # -- 15.18 ---------------------------------------------------------
    def test_15_18_missing_calculation_id_swallows_and_writes_no_row(self):
        """``LexLogger().add_text(...).log()`` with no operation_context
        and no model on the LIFO stack must:

        1. Not raise.
        2. Not write a CalculationLog row.
        """
        try:
            LexLogger().add_text("orphan").log()
        except Exception as exc:  # pragma: no cover — defensive
            self.fail(
                f"LexLogger.log() must swallow context-resolution errors, "
                f"but raised {type(exc).__name__}: {exc}"
            )

        self.assertEqual(
            CalculationLog.objects.count(), 0,
            "No row must be written when calculation context is missing.",
        )

