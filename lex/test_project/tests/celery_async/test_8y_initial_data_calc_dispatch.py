"""Initial-data-upload calculations honour the normal Celery-dispatch contract.

Intent: a ``CalculationModel`` seeded by the initial-data loader is a *root*
    calculation and must follow the **same** dispatch rules as a calculation
    triggered from the UI/REST layer (see ``docs/features/data-pipeline`` +
    PR #544 "dispatch every root calculation to Celery, decorator-optional"):

      * when ``CELERY_ACTIVE=true`` and the broker is reachable, the seeded
        calculation is dispatched to a Celery worker as a job — even when the
        model's ``calculate()`` carries no ``@lex_shared_task`` decorator (it
        rides the generic ``calc_and_save`` task), and **regardless of whether
        the loader established an explicit ``WaitForTasks`` scope** (the two
        ``apps.async_ready`` dispatch branches must behave identically);
      * when Celery is off or the broker is unreachable, it falls back to the
        same synchronous in-process execution a normal calculation would use;
      * the documented arming contract holds — only a seed object carrying
        ``"is_calculated": "IN_PROGRESS"`` triggers a calculation at all
        (``docs/lex_topics/16-initial-data-upload.md``).

    Why a regression matters: initial data upload is the first thing a fresh
    deployment runs. If seeded calculations silently ran inline on the startup
    thread instead of dispatching as jobs (or vice-versa), customers would see
    a different runtime profile during seeding than during normal operation —
    exactly the "two runtime profiles" divergence #544 removed — masking slow
    calculations as startup hangs.

Cluster 8y — scenarios 8.116–8.120. Type: I.
Covers: lex/lex_app/tests/ProcessAdminTestCase.py (seed loader),
        lex/core/models/CalculationModel.py (calculate_hook / should_use_celery /
        dispatch_calculation_task), lex/lex_app/celery_tasks.py (calc_and_save).
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8y_initial_data_calc_dispatch.py -v
"""

from __future__ import annotations

import os
import unittest
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08y_InitialDataCalculationDispatch(E2ETestCase):
    """Cluster 8y: seeded calculations follow the normal dispatch contract."""

    e2e_models = [CelerySyncCalc]

    # ── helpers ──────────────────────────────────────────────────────

    @contextmanager
    def _celery_boundary(self, *, celery_active: bool, broker_reachable: bool = True):
        """Pin the Celery decision boundary without a live broker.

        Controls the two inputs of ``should_use_celery`` (the ``CELERY_ACTIVE``
        env flag and the broker reachability probe) and records the dispatch
        vs. synchronous-execution seams so a test can assert which path the
        seeded calculation took:

          * ``calc_and_save`` — the generic task an undecorated root
            calculation is dispatched through (the "job" path);
          * ``execute_calculation_sync`` — the in-process fallback the same
            calculation uses when Celery is unavailable.
        """
        fake_result = MagicMock(name="AsyncResult", id="task-8y")
        fake_calc_and_save = MagicMock(name="calc_and_save")
        fake_calc_and_save.delay = MagicMock(return_value=fake_result)
        sync_spy = MagicMock(name="execute_calculation_sync", return_value=None)

        inspect_mock = MagicMock(name="broker_inspect")
        if not broker_reachable:
            inspect_mock.side_effect = ConnectionError("broker unreachable")

        env = {"CELERY_ACTIVE": "true" if celery_active else "False"}
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, env, clear=False))
            stack.enter_context(
                patch("celery.current_app.control.inspect", inspect_mock)
            )
            stack.enter_context(
                patch("lex.lex_app.celery_tasks.calc_and_save", fake_calc_and_save)
            )
            stack.enter_context(
                patch(
                    "lex.lex_app.celery_tasks.register_task_with_context",
                    side_effect=lambda r: r,
                )
            )
            stack.enter_context(
                patch.object(CelerySyncCalc, "execute_calculation_sync", sync_spy)
            )
            yield SimpleNamespace(calc_and_save=fake_calc_and_save, sync=sync_spy)

    def _run_seed_load(self, seed_objects):
        """Drive the real initial-data seed loader over an in-memory plan.

        ``get_test_data`` is the loader's file-IO boundary (it reads and
        flattens the initial-data JSON); replacing it with an in-memory list is
        equivalent to providing that JSON file, while every downstream step —
        ``klass(**parameters)``, the ``OperationContext`` / ``model_logging_context``
        wrappers, and ``save()`` — runs for real.
        """
        loader = ProcessAdminTestCase()
        loader.get_test_data = lambda: list(seed_objects)
        loader.setUpCloudStorage(
            {"CelerySyncCalc": CelerySyncCalc}, audit_logger=None
        )
        return loader

    @staticmethod
    def _seed(name, *, in_progress):
        params = {"name": name}
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

    # ── 8.116 ────────────────────────────────────────────────────────
    def test_8_116_seeded_undecorated_calc_dispatches_as_job(self) -> None:
        """
        Scenario 8.116: undecorated seeded calc dispatches to Celery.

        Given: an undecorated ``CalculationModel`` seeded with
               ``is_calculated=IN_PROGRESS``, ``CELERY_ACTIVE=true`` and a
               reachable broker, and **no** explicit ``WaitForTasks`` scope
               (the legacy-threading ``apps.async_ready`` branch).
        When:  the initial-data loader saves the seeded object.
        Then:  the calculation is dispatched as a job through the generic
               ``calc_and_save`` task (decorator-optional) and is NOT executed
               inline — the same contract as a normal root calculation.
        """
        with self._celery_boundary(celery_active=True, broker_reachable=True) as m:
            loader = self._run_seed_load(self._seed("seed-undecorated", in_progress=True))

        instance = loader.tagged_objects["c1"]
        m.calc_and_save.delay.assert_called_once()
        dispatched_models, _ = m.calc_and_save.delay.call_args
        self.assertEqual(
            list(dispatched_models[0]),
            [instance],
            "seed loader must dispatch the seeded instance through calc_and_save",
        )
        self.assertEqual(
            m.sync.call_count,
            0,
            "seeded calc must NOT run inline when Celery dispatch is available",
        )
        self.assertIsNotNone(
            instance.pk, "the seeded row must still be persisted before dispatch"
        )

    # ── 8.117 ────────────────────────────────────────────────────────
    def test_8_117_seed_under_waitfortasks_scope_still_dispatches(self) -> None:
        """
        Scenario 8.117: seeding inside a WaitForTasks scope still dispatches.

        Given: the same seeded calc, but the loader runs inside an explicit
               ``WaitForTasks`` scope — the MQ/Worker ``apps.async_ready``
               branch that wraps ``load_data`` in ``WaitForTasks()``.
        When:  the loader saves the seeded object.
        Then:  the calculation is still dispatched as a job (branch parity:
               both ``async_ready`` dispatch branches produce a Celery job, not
               an inline run) — so the runtime profile does not depend on the
               deployment architecture flag.
        """
        from lex.lex_app.celery_tasks import WaitForTasks

        with self._celery_boundary(celery_active=True, broker_reachable=True) as m:
            with WaitForTasks():
                loader = self._run_seed_load(
                    self._seed("seed-wft", in_progress=True)
                )

        self.assertEqual(
            m.calc_and_save.delay.call_count,
            1,
            "seeded calc under a WaitForTasks scope must still dispatch as a job",
        )
        self.assertEqual(
            m.sync.call_count,
            0,
            "the WaitForTasks (MQ/Worker) branch must not run the seed calc inline",
        )
        self.assertIsNotNone(loader.tagged_objects["c1"].pk)

    # ── 8.118 ────────────────────────────────────────────────────────
    def test_8_118_celery_off_seed_runs_synchronously(self) -> None:
        """
        Scenario 8.118: with Celery off the seeded calc runs synchronously.

        Given: the same seeded calc but ``CELERY_ACTIVE`` is not ``"true"``.
        When:  the loader saves the seeded object.
        Then:  ``should_use_celery()`` is False, so the calculation falls back
               to the same in-process synchronous execution a normal
               calculation uses — no Celery job is dispatched.
        """
        with self._celery_boundary(celery_active=False) as m:
            loader = self._run_seed_load(self._seed("seed-sync", in_progress=True))

        self.assertEqual(
            m.calc_and_save.delay.call_count,
            0,
            "no Celery job may be dispatched for a seeded calc when Celery is off",
        )
        m.sync.assert_called_once()
        self.assertIsNotNone(loader.tagged_objects["c1"].pk)

    # ── 8.119 ────────────────────────────────────────────────────────
    def test_8_119_broker_unreachable_seed_falls_back_to_sync(self) -> None:
        """
        Scenario 8.119: unreachable broker degrades the seed calc to sync.

        Given: ``CELERY_ACTIVE=true`` but the broker reachability probe raises
               (broker down) while seeding.
        When:  the loader saves the seeded object.
        Then:  ``should_use_celery()`` returns False and the seeded calc
               executes synchronously in-process — the same graceful
               degradation as a normal calculation — instead of dispatching to
               an unreachable worker.
        """
        with self._celery_boundary(celery_active=True, broker_reachable=False) as m:
            loader = self._run_seed_load(self._seed("seed-degrade", in_progress=True))

        self.assertEqual(
            m.calc_and_save.delay.call_count,
            0,
            "an unreachable broker must not receive a dispatched seed calc",
        )
        m.sync.assert_called_once()
        self.assertIsNotNone(loader.tagged_objects["c1"].pk)

    # ── 8.120 ────────────────────────────────────────────────────────
    def test_8_120_seed_without_in_progress_does_not_calculate(self) -> None:
        """
        Scenario 8.120: a seed object without IN_PROGRESS is not a calc trigger.

        Given: a ``CalculationModel`` seeded WITHOUT
               ``"is_calculated": "IN_PROGRESS"`` (Celery active and reachable).
        When:  the loader saves the seeded object.
        Then:  the documented arming contract holds — ``save()`` is not a
               calculation trigger, so nothing is dispatched and nothing runs
               inline; the row persists in its default ``NOT_CALCULATED`` state.
        """
        with self._celery_boundary(celery_active=True, broker_reachable=True) as m:
            loader = self._run_seed_load(self._seed("seed-plain", in_progress=False))

        instance = loader.tagged_objects["c1"]
        self.assertEqual(
            m.calc_and_save.delay.call_count,
            0,
            "a seed object without IN_PROGRESS must not dispatch a calculation",
        )
        self.assertEqual(
            m.sync.call_count,
            0,
            "a seed object without IN_PROGRESS must not run a calculation inline",
        )
        self.assertIsNotNone(instance.pk, "the seeded row must still be persisted")
        self.assertEqual(
            instance.is_calculated,
            CalculationModel.NOT_CALCULATED,
            "an un-armed seed object must remain in its default NOT_CALCULATED state",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
