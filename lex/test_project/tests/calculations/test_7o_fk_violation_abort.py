"""Cluster 7o: Any error during batch calculation aborts the entire batch.

Intent: when a calculation step raises any error (FK integrity, runtime
exception, etc.), the framework must stop immediately and must not
continue processing later models in the same batch.
Cluster 7o — scenarios 7.176–7.177. Type: I.
Covers: `lex/core/mixins/CalculatedModelMixin.py`, `lex/lex_app/celery_tasks.py`.
Run: python -m lex pytest lex/test_project/tests/calculations/test_7o_fk_violation_abort.py -v
"""

from __future__ import annotations

import pytest
from django.db import connection

from lex.core.exceptions import CalculatedModelError
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import (
    FKAbortTarget,
    FKAbortWrite,
    FKViolationAbortCalc,
    GeneralErrorAbortCalc,
    GeneralErrorAbortMarker,
)

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


class TestCluster07o_GeneralErrorAbort(E2ETestCase):
    """Cluster 7o: Any calculation error must abort the whole batch."""

    e2e_models = [GeneralErrorAbortMarker, GeneralErrorAbortCalc]

    def setUp(self):
        super().setUp()
        GeneralErrorAbortCalc._steps = ["first", "explode", "last"]

    def test_07_177_general_error_aborts_batch_immediately(self):
        """
        Scenario 7.177: RuntimeError inside calculate() aborts the full batch.
        Given: a 3-step create() batch (first, explode, last) where "explode"
               raises a RuntimeError.
        When: create() processes the batch synchronously.
        Then: the error propagates and "last" step never runs.
        """
        with self.assertRaises(CalculatedModelError):
            GeneralErrorAbortCalc.create()

        # The first step should have completed before the error
        self.assertTrue(
            GeneralErrorAbortMarker.objects.filter(marker="first").exists(),
            "The first step should complete before the error is hit",
        )
        # The last step must NOT have run
        self.assertFalse(
            GeneralErrorAbortMarker.objects.filter(marker="last").exists(),
            "Batch must abort immediately on any error; later steps must not persist",
        )
        # Only the "first" marker should exist
        self.assertEqual(
            list(GeneralErrorAbortMarker.objects.order_by("id").values_list("marker", flat=True)),
            ["first"],
            "Only writes from pre-error steps are allowed",
        )
