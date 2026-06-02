"""Cluster 7o: ForeignKey integrity errors abort calculated batches.

Intent: a calculation that hits an unhandled FK integrity violation must
stop immediately; the framework must not continue processing later models
in the same `CalculatedModelMixin.create()` batch after that data-integrity
failure.
Cluster 7o — scenarios 7.176–7.176. Type: I.
Covers: `lex/core/mixins/CalculatedModelMixin.py`, `lex/lex_app/celery_tasks.py`.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7o_fk_violation_abort.py -v
"""

from __future__ import annotations

import pytest
from django.db import connection

from lex.core.exceptions import CalculatedModelError
from lex.tests.e2e._e2e_test_case import E2ETestCase

from .models import FKAbortTarget, FKAbortWrite, FKViolationAbortCalc

pytestmark = pytest.mark.calculations


class TestCluster07o_ForeignKeyAbort(E2ETestCase):
    """Cluster 7o: FK violations must abort the whole create() computation."""

    e2e_models = [FKAbortTarget, FKAbortWrite, FKViolationAbortCalc]

    def setUp(self):
        super().setUp()
        target = FKAbortTarget.objects.create(name="ok-target")
        FKViolationAbortCalc._steps = ["before", "bad", "after"]
        FKViolationAbortCalc._valid_target_id = target.pk
        if connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA foreign_keys = ON")

    def tearDown(self):
        if connection.vendor == "sqlite":
            with connection.cursor() as cursor:
                cursor.execute("PRAGMA foreign_keys = OFF")
        super().tearDown()

    def test_07_176_foreign_key_violation_aborts_batch_immediately(self):
        """
        Scenario 7.176: FK violation inside calculate() aborts the full batch.
        Given: a 3-step create() batch (before, bad, after) where "bad" writes
               a LexModel row with an invalid FK.
        When: create() processes the batch synchronously.
        Then: the FK integrity error is raised and the "after" step never runs.
        """
        with self.assertRaises(CalculatedModelError):
            FKViolationAbortCalc.create()

        self.assertTrue(
            FKViolationAbortCalc.objects.filter(step="before").exists(),
            "The first step should complete before the FK violation is hit",
        )
        self.assertFalse(
            FKViolationAbortCalc.objects.filter(step="after").exists(),
            "Batch must abort immediately on FK violation; later steps must not persist",
        )
        self.assertEqual(
            list(FKAbortWrite.objects.order_by("id").values_list("marker", flat=True)),
            ["before"],
            "Only writes from pre-error steps are allowed; post-error writes indicate silent continuation",
        )
