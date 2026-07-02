"""Idempotent complete-sync fallback for the Celery fan-out (Report 2 fix).

Intent: when ``CeleryTaskDispatcher.dispatch_calculation_groups`` fails to set up
its Celery fan-out part-way (production Report 2 — a connection storm under high
``--concurrency``: ``FATAL: sorry, too many clients already``), it falls back to
running every model INLINE via ``calc_and_save_sync(all_models)``. The trap: the
model instances handed to that fallback were resolved against the DB at T0 by the
upstream stage-2 prepare (``_prepare_models_for_processing``), *before* dispatch —
when none of their defining-fields rows existed yet, so each had its pk reset for
a fresh INSERT. But by the time the storm trips, sibling child tasks that DID
dispatch successfully may have ALREADY committed some of those rows. A blind
``save()`` in the fallback then INSERTs a *second* row with the same defining
fields and Postgres rejects it: ``duplicate key value violates unique constraint
"defining_fields_<Model>"`` (Report 2 — 558 models, EndBalance).

The fix (Option A) makes ``calc_and_save_sync`` re-resolve each model against the
DB immediately before writing — the same
``delete_models_with_same_defining_fields()`` step the normal and streaming paths
already run per model. So at fallback time: 1 existing row → return it (save
UPDATEs), 0 → pk reset (save INSERTs). This keeps the 7q combinatorial fan-out
running in parallel (no inline-inside-worker collapse) while making its failure
fallback safe. The resolver is itself idempotent, so re-preparing an
as-yet-uncommitted model is a no-op.

Cluster 8ac — scenarios 8.139–8.141. Type: I (real DB — CombinatorialCalc's
``defining_fields=[region, category]`` yields a real UNIQUE constraint).
Covers: lex/core/mixins/CalculatedModelMixin.py (calc_and_save_sync
        re-resolve-before-save), lex/core/tasks/CeleryTaskDispatcher.py
        (complete-sync fallback path).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8ac_sync_fallback_idempotent.py -v
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

import pytest

from lex.core.mixins.CalculatedModelMixin import calc_and_save_sync
from lex.core.tasks.CeleryTaskDispatcher import CeleryTaskDispatcher
from lex.test_project.tests._e2e_test_case import E2ETestCase

from lex.test_project.tests.calculations.models import ALL_MODELS, CombinatorialCalc

pytestmark = pytest.mark.celery_async


class TestCluster08ac_SyncFallbackIdempotent(E2ETestCase):
    """Cluster 8ac: the complete-sync fallback re-resolves each model against
    the DB before saving, so a row a sibling task already committed is UPDATEd
    (not blindly re-INSERTed into a duplicate-key violation)."""

    e2e_models = ALL_MODELS

    def setUp(self) -> None:
        super().setUp()
        # Neutralise the class-level failure knob other clusters flip.
        CombinatorialCalc.fail_for_region = None

    def _fresh_instance(self, region: str, category: str) -> CombinatorialCalc:
        """A brand-new unsaved instance (pk=None) — the shape a model has after
        stage-2 prepare reset its pk for a fresh INSERT at T0, before any sibling
        committed the row."""
        return CombinatorialCalc(region=region, category=category)

    # -- 8.139 ---------------------------------------------------------
    def test_8_139_sync_fallback_updates_row_a_sibling_already_committed(self) -> None:
        """Scenario 8.139: calc_and_save_sync on a model whose defining-fields
        row a sibling ALREADY committed.
        Given: an existing (US, A) row (committed by a sibling child task) and a
               fresh unsaved (US, A) instance whose pk was reset at T0.
        When:  calc_and_save_sync([instance]) runs (the complete-sync fallback).
        Then:  it re-resolves to the existing row and UPDATEs it — no
               IntegrityError, exactly ONE (US, A) row, and it is the same pk as
               the pre-committed row (proving UPDATE, not a second INSERT).
        """
        existing = CombinatorialCalc.objects.create(
            region="US", category="A", name="stale-from-sibling"
        )

        instance = self._fresh_instance("US", "A")
        calc_and_save_sync([instance])

        rows = CombinatorialCalc.objects.filter(region="US", category="A")
        self.assertEqual(
            rows.count(), 1,
            "Exactly one (US, A) row must remain — the fallback must UPDATE the "
            "sibling's committed row, not INSERT a duplicate.",
        )
        self.assertEqual(
            rows.first().pk, existing.pk,
            "The surviving row must be the sibling's pre-committed row (UPDATE), "
            "not a freshly INSERTed one.",
        )
        self.assertEqual(
            rows.first().name, "US-A",
            "calculate() must have run against the re-resolved row and set name.",
        )

    # -- 8.140 ---------------------------------------------------------
    def test_8_140_sync_fallback_inserts_a_fresh_model(self) -> None:
        """Scenario 8.140: calc_and_save_sync on a model with no existing row.
        Given: no (EU, B) row exists and a fresh unsaved (EU, B) instance.
        When:  calc_and_save_sync([instance]) runs.
        Then:  it INSERTs exactly one (EU, B) row — the re-resolve step is a
               no-op when nothing is there, so ordinary first-write behaviour is
               unchanged.
        """
        self.assertFalse(
            CombinatorialCalc.objects.filter(region="EU", category="B").exists(),
            "Precondition: no (EU, B) row before the fallback runs.",
        )

        instance = self._fresh_instance("EU", "B")
        calc_and_save_sync([instance])

        rows = CombinatorialCalc.objects.filter(region="EU", category="B")
        self.assertEqual(
            rows.count(), 1,
            "A fresh model with no existing row must INSERT exactly one row.",
        )
        self.assertEqual(
            rows.first().name, "EU-B",
            "calculate() must have run and set name on the inserted row.",
        )

    # -- 8.141 ---------------------------------------------------------
    def test_8_141_dispatch_complete_fallback_no_duplicate_key(self) -> None:
        """Scenario 8.141: end-to-end dispatch where the Celery fan-out setup
        blows up (simulated connection storm) and one row was already committed.
        Given: an existing (US, A) row (a sibling that dispatched before the
               storm) and a group of fresh (US, A) + (EU, B) instances whose pks
               were reset at T0.
        When:  dispatch_calculation_groups runs but _dispatch_single_group raises
               (the storm), driving the COMPLETE-sync fallback over all models.
        Then:  the fallback completes with NO duplicate-key IntegrityError — the
               (US, A) row is UPDATEd and (EU, B) is INSERTed, one row each.
        """
        existing = CombinatorialCalc.objects.create(
            region="US", category="A", name="stale-from-sibling"
        )

        group = [self._fresh_instance("US", "A"), self._fresh_instance("EU", "B")]

        # Simulate the connection storm crashing per-group dispatch setup: a
        # plain Exception out of _dispatch_single_group propagates to
        # dispatch_calculation_groups' setup-failure handler, which flattens all
        # groups and runs the complete-sync fallback — the exact production path.
        with patch.object(
            CeleryTaskDispatcher,
            "_dispatch_single_group",
            side_effect=RuntimeError(
                'connection to server at "localhost" port 5432 failed: FATAL: '
                "sorry, too many clients already"
            ),
        ):
            CeleryTaskDispatcher.dispatch_calculation_groups(
                [group], context={"calculation_id": "test-8-141", "request_obj": {}}
            )

        us_a = CombinatorialCalc.objects.filter(region="US", category="A")
        eu_b = CombinatorialCalc.objects.filter(region="EU", category="B")
        self.assertEqual(
            us_a.count(), 1,
            "The (US, A) row must be UPDATEd by the fallback, not duplicated — "
            "this is the Report 2 duplicate-key regression gate.",
        )
        self.assertEqual(
            us_a.first().pk, existing.pk,
            "The surviving (US, A) row must be the sibling's committed row.",
        )
        self.assertEqual(
            eu_b.count(), 1,
            "The (EU, B) model had no prior row and must be INSERTed exactly once.",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
