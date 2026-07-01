"""Initial-data seed calculations run on the dedicated calculation thread pool.

Intent: a ``CalculationModel`` seeded by the initial-data loader is a *root*
    calculation and must be executed the **same** way the live request path
    (``lex.api.views.model_entries.One.update``) executes one — **off the
    thread that triggered it**, on the shared ``_calculation_executor``
    (``lex-calc``) pool — rather than inline on the bootstrap/loader thread.

    Why a regression matters: ``One.update`` deliberately offloads
    ``calculate_hook`` to ``_calculation_executor`` so a heavy calculation
    never blocks the single ASGI/web thread (it returns HTTP 202 and lets the
    pool finish). The initial-data loader historically ran the seeded
    calculation *inline* on whatever thread drove ``setUpCloudStorage`` /
    ``setUp`` — the bootstrap thread of a fresh deployment. That is the very
    "calculation hogs the triggering thread" asymmetry the request path was
    built to avoid: a slow seeded calculation would stall startup instead of
    running as a pooled job.

    The fix mirrors ``One.update`` exactly: defer the hook, commit the
    ``IN_PROGRESS`` row, submit ``calculate_hook`` to ``_calculation_executor``
    — but, unlike the fire-and-forget request path, the loader **blocks on the
    future** before returning, because initial-data actions are processed
    strictly in order and a later action may reference an earlier
    calculation's result (``docs/features/data-pipeline/initial data.md``). So
    the calculation still runs on the pool, off the loader thread, while the
    documented ordering guarantee is preserved.

Cluster 8z — scenarios 8.121–8.124. Type: E.
Covers: lex/lex_app/tests/ProcessAdminTestCase.py (_save_seed_instance — the
        seed-loader offload), exercising the real
        lex/core/models/CalculationModel.py calculate_hook / execute_calculation_sync.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8z_initial_data_calc_executor.py -v
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import pytest

from lex.core.models.CalculationModel import (
    CalculationModel,
    CalculationModelException,
    _calculation_executor,
)
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08z_InitialDataCalcExecutor(E2ETestCase):
    """Cluster 8z: seeded calculations run on the ``_calculation_executor`` pool.

    ``E2ETestCase`` pins ``CELERY_ACTIVE=False``, so the seeded calculation
    actually *executes* (the synchronous in-process path) rather than being
    dispatched to a broker — which is exactly what lets these tests observe
    *which thread* the real calculation body runs on.
    """

    e2e_models = [CelerySyncCalc]

    # ── helpers ──────────────────────────────────────────────────────

    def _run_seed_load(self, seed_objects):
        """Drive the real initial-data seed loader over an in-memory plan.

        ``get_test_data`` is the loader's file-IO boundary (it reads and
        flattens the initial-data JSON); replacing it with an in-memory list is
        equivalent to providing that JSON file, while every downstream step —
        ``klass(**parameters)``, ``_save_seed_instance`` (the offload under
        test), and the real ``calculate_hook`` — runs for real.
        """
        loader = ProcessAdminTestCase()
        loader.get_test_data = lambda: list(seed_objects)
        loader.setUpCloudStorage(
            {"CelerySyncCalc": CelerySyncCalc}, audit_logger=None
        )
        return loader

    @staticmethod
    def _seed(name, *, in_progress, should_fail=False):
        params = {"name": name, "should_fail": should_fail}
        if in_progress:
            params["is_calculated"] = CalculationModel.IN_PROGRESS
        return [
            {
                "class": "CelerySyncCalc",
                "action": "create",
                "tag": "c1",
                "parameters": params,
            }
        ]

    # ── 8.121 ────────────────────────────────────────────────────────
    def test_8_121_seeded_calc_runs_on_calculation_pool(self) -> None:
        """
        Scenario 8.121: a triggered seed calc executes on the lex-calc pool.

        Given: an undecorated ``CalculationModel`` seeded with
               ``is_calculated=IN_PROGRESS`` (Celery off, so it runs in-process).
        When:  the initial-data loader saves the seeded object.
        Then:  the calculation body runs on a ``_calculation_executor``
               (``lex-calc``) worker thread — NOT inline on the loader thread —
               exactly as ``One.update`` offloads it; the executor's
               ``submit`` is used once and the row reaches ``SUCCESS``.
        """
        loader_thread = threading.current_thread().name
        recorded: dict = {}

        def _record_thread_calculate(inner_self):
            recorded["thread"] = threading.current_thread().name

        with patch.object(CelerySyncCalc, "calculate", _record_thread_calculate):
            with patch.object(
                _calculation_executor, "submit", wraps=_calculation_executor.submit
            ) as submit_spy:
                loader = self._run_seed_load(
                    self._seed("seed-pool", in_progress=True)
                )

        instance = loader.tagged_objects["c1"]
        self.assertIn(
            "thread",
            recorded,
            "the seeded calculation body must have actually executed",
        )
        self.assertTrue(
            recorded["thread"].startswith("lex-calc"),
            "seeded calc must run on a _calculation_executor (lex-calc) thread, "
            f"got {recorded['thread']!r}",
        )
        self.assertNotEqual(
            recorded["thread"],
            loader_thread,
            "seeded calc must NOT run inline on the loader thread",
        )
        submit_spy.assert_called_once()
        self.assertEqual(
            instance.is_calculated,
            CalculationModel.SUCCESS,
            "a successful seeded calculation must land in SUCCESS",
        )
        self.assertIsNotNone(
            instance.pk, "the seeded row must be persisted before dispatch"
        )

    # ── 8.122 ────────────────────────────────────────────────────────
    def test_8_122_loader_blocks_on_future_preserving_order(self) -> None:
        """
        Scenario 8.122: the loader waits on each pooled calc (ordered seeding).

        Given: two seeded calcs processed in plan order — ``slow`` (whose
               ``calculate`` sleeps) then ``fast`` (no sleep) — both
               ``IN_PROGRESS``.
        When:  the loader saves them.
        Then:  the bodies complete in *plan* order ``[slow, fast]``, proving the
               loader blocks on each calculation's future before starting the
               next action. If the loader fire-and-forgot to the pool, the
               no-sleep ``fast`` calc would finish first (``[fast, slow]``) —
               so this ordering is the documented guarantee that a later
               initial-data action may rely on an earlier one's result.
        """
        order: list = []

        def _ordered_calculate(inner_self):
            if inner_self.name == "slow":
                time.sleep(0.2)
            order.append(inner_self.name)

        seeds = [
            {
                "class": "CelerySyncCalc",
                "action": "create",
                "tag": "slow",
                "parameters": {
                    "name": "slow",
                    "is_calculated": CalculationModel.IN_PROGRESS,
                },
            },
            {
                "class": "CelerySyncCalc",
                "action": "create",
                "tag": "fast",
                "parameters": {
                    "name": "fast",
                    "is_calculated": CalculationModel.IN_PROGRESS,
                },
            },
        ]

        with patch.object(CelerySyncCalc, "calculate", _ordered_calculate):
            self._run_seed_load(seeds)

        self.assertEqual(
            order,
            ["slow", "fast"],
            "the loader must block on each pooled calc, preserving action order; "
            f"got {order!r} (a fire-and-forget pool would yield ['fast', 'slow'])",
        )

    # ── 8.123 ────────────────────────────────────────────────────────
    def test_8_123_non_trigger_seed_does_not_touch_pool(self) -> None:
        """
        Scenario 8.123: a seed without IN_PROGRESS never reaches the pool.

        Given: a ``CalculationModel`` seeded WITHOUT
               ``is_calculated=IN_PROGRESS`` (an un-armed seed object).
        When:  the loader saves it.
        Then:  ``save()`` is not a calculation trigger, so the offload is
               skipped entirely — ``_calculation_executor.submit`` is never
               called — the row simply persists in its default
               ``NOT_CALCULATED`` state. (Mirrors the request-path contract:
               only an ``IN_PROGRESS`` save arms a calculation.)
        """
        with patch.object(
            _calculation_executor, "submit", wraps=_calculation_executor.submit
        ) as submit_spy:
            loader = self._run_seed_load(
                self._seed("seed-plain", in_progress=False)
            )

        instance = loader.tagged_objects["c1"]
        submit_spy.assert_not_called()
        self.assertIsNotNone(instance.pk, "the seeded row must still be persisted")
        self.assertEqual(
            instance.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "an un-armed seed object must remain in its default NOT_CALCULATED state",
        )

    # ── 8.124 ────────────────────────────────────────────────────────
    def test_8_124_failing_pooled_seed_propagates_to_loader(self) -> None:
        """
        Scenario 8.124: a pooled seed calc's failure surfaces to the loader.

        Given: a seeded calc whose ``calculate`` raises (``should_fail=True``),
               armed ``IN_PROGRESS``.
        When:  the loader saves it.
        Then:  because the loader blocks on the future, the exception raised on
               the pool thread is re-raised on the loader thread — it does NOT
               vanish in a fire-and-forget worker. ``setUpCloudStorage``
               propagates it (as ``CalculationModelException`` wrapping the
               original error) and the row is left in ``ERROR``; the offload
               was still used (``submit`` called once).
        """
        with patch.object(
            _calculation_executor, "submit", wraps=_calculation_executor.submit
        ) as submit_spy:
            with self.assertRaises(CalculationModelException) as caught:
                self._run_seed_load(
                    self._seed("seed-boom", in_progress=True, should_fail=True)
                )

        submit_spy.assert_called_once()
        self.assertIn(
            "failing on purpose",
            str(caught.exception.__cause__),
            "the original calculate() error must be chained onto the "
            "CalculationModelException surfaced to the loader",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
