"""Initial-data seed calculations don't run slower than the live request path.

Intent: the initial-data loader offloads a seeded ``CalculationModel`` to the
    SAME ``_calculation_executor`` (``lex-calc``) pool the live request path
    (``lex.api.views.model_entries.One.update``) uses — see sibling cluster 8z.
    The only intended difference is *when control returns*: the request path
    fires-and-returns HTTP 202, while the loader blocks on the future because
    initial-data actions are ordered (a later action may use an earlier
    calculation's result, ``docs/features/data-pipeline/initial data.md``).

    Because both paths submit the identical ``calculate_hook`` to the identical
    10-worker pool, the seed path must not be *slower per calculation* than the
    request path. The regression this batch guards against is a seed loader that
    quietly reverts to running the calculation **inline** on the bootstrap
    thread (the original pre-fix behaviour): that would (a) serialize seeding
    onto one lane instead of the shared pool, (b) execute the calculation body
    twice if the offload were *also* kept (double work), and (c) re-introduce
    the "calculation hogs the triggering thread → server not ready during heavy
    calculations" stall the pool offload was built to remove (commit bde8dde).

    Why this is not already covered by 8z: 8z pins *that* the seed calc runs on
    the pool, blocks in order, skips non-triggers, and propagates failures. It
    does NOT prove the pool's **concurrency budget is preserved** (seeding one
    calc doesn't monopolize the pool), that the body runs **exactly once** (no
    inline duplicate), or that the dispatch **overhead is negligible** versus
    running the same body bare on the pool. Those three are the "no slowdown"
    surfaces and live here.

Cluster 8ab — scenarios 8.129–8.131. Type: E.
Covers: lex/lex_app/tests/ProcessAdminTestCase.py (_save_seed_instance — the
        seed-loader offload), exercising the real
        lex/core/models/CalculationModel.py _calculation_executor (lex-calc) pool.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8ab_initial_data_no_slowdown.py -v
"""

from __future__ import annotations

import threading
import time
import unittest
from unittest.mock import patch

import pytest

from lex.core.models.CalculationModel import (
    CalculationModel,
    _calculation_executor,
)
from lex.lex_app.tests.ProcessAdminTestCase import ProcessAdminTestCase
from lex.test_project.tests._e2e_test_case import E2ETestCase

from .models import CelerySyncCalc

pytestmark = pytest.mark.celery_async


class TestCluster08ab_InitialDataNoSlowdown(E2ETestCase):
    """Cluster 8ab: the seed-calc offload keeps the pool's concurrency and
    adds no per-calculation slowdown versus the live request path.

    ``E2ETestCase`` pins ``CELERY_ACTIVE=False`` so the seeded calculation
    actually executes in-process (rather than being dispatched to a broker),
    which is exactly what lets these tests observe *which thread* runs the body
    and *how long* the offload takes.
    """

    e2e_models = [CelerySyncCalc]

    # ── helpers (mirror cluster 8z so both batches drive the real loader) ──

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
        loader.setUpCloudStorage({"CelerySyncCalc": CelerySyncCalc}, audit_logger=None)
        return loader

    @staticmethod
    def _seed(name):
        return [
            {
                "class": "CelerySyncCalc",
                "action": "create",
                "tag": "c1",
                "parameters": {
                    "name": name,
                    "should_fail": False,
                    "is_calculated": CalculationModel.IN_PROGRESS,
                },
            }
        ]

    # ── 8.129 ────────────────────────────────────────────────────────
    def test_8_129_seed_calc_shares_pool_without_monopolizing_it(self) -> None:
        """
        Scenario 8.129: a seed calc occupies one pool worker but leaves the
        pool free for concurrent work — concurrency budget is preserved.

        Given: a seeded ``CalculationModel`` (IN_PROGRESS) whose ``calculate``
               body parks on a barrier once it reaches the pool.
        When:  the loader drives it on a background thread (so its blocking
               wait-on-future doesn't freeze the test thread), and while that
               seed calc is parked the test submits an INDEPENDENT task to the
               same ``_calculation_executor``.
        Then:  the independent task runs to completion on a *different*
               ``lex-calc`` worker while the seed calc is still held — proving
               the seed path runs on the shared pool and does not serialize it
               onto a single lane. A regression that ran the seed calc inline on
               the loader thread would surface as a non-``lex-calc`` body thread.
        """
        seed_calc_in_pool = threading.Event()
        release_seed_calc = threading.Event()
        seed_calc_thread: dict = {}

        def _parking_calculate(inner_self):
            seed_calc_thread["name"] = threading.current_thread().name
            seed_calc_in_pool.set()
            # Hold this pool worker until the probe has proven concurrency.
            if not release_seed_calc.wait(timeout=5):
                raise AssertionError("probe never released the parked seed calc")

        seed_done = threading.Event()
        seed_error: dict = {}

        def _drive_seed():
            try:
                with patch.object(CelerySyncCalc, "calculate", _parking_calculate):
                    self._run_seed_load(self._seed("seed-concurrent"))
            except BaseException as exc:  # surface to the asserting thread
                seed_error["exc"] = exc
            finally:
                seed_done.set()

        driver = threading.Thread(target=_drive_seed, name="seed-loader-driver")
        driver.start()
        try:
            self.assertTrue(
                seed_calc_in_pool.wait(timeout=5),
                "the seeded calculation body never reached the pool",
            )

            # While the seed calc is parked on a pool worker, an independent
            # submission to the SAME pool must still run and finish. If the seed
            # path had collapsed the pool to a single lane, result() would hang.
            probe = _calculation_executor.submit(
                lambda: threading.current_thread().name
            )
            probe_thread = probe.result(timeout=5)
        finally:
            release_seed_calc.set()

        self.assertTrue(seed_done.wait(timeout=5), "the seed loader never returned")
        if "exc" in seed_error:
            raise seed_error["exc"]

        self.assertTrue(
            seed_calc_thread["name"].startswith("lex-calc"),
            "the seed calc must run on a _calculation_executor (lex-calc) worker, "
            f"not inline on the loader thread; got {seed_calc_thread['name']!r}",
        )
        self.assertTrue(
            probe_thread.startswith("lex-calc"),
            f"the concurrent probe must run on the lex-calc pool; got {probe_thread!r}",
        )
        self.assertNotEqual(
            probe_thread,
            seed_calc_thread["name"],
            "the probe must run on a DIFFERENT pool worker than the parked seed "
            "calc — that is the proof the pool's concurrency budget is preserved",
        )

    # ── 8.130 ────────────────────────────────────────────────────────
    def test_8_130_seed_calc_body_runs_exactly_once_on_pool(self) -> None:
        """
        Scenario 8.130: the seeded calculation body executes exactly once, and
        only on a pool worker — never an inline duplicate.

        Given: a seeded ``CalculationModel`` (IN_PROGRESS).
        When:  the loader saves it.
        Then:  ``calculate`` is invoked exactly once, on a ``lex-calc`` thread,
               and never on the loader thread. A regression that kept the pool
               offload but *also* ran the body inline (e.g. a stray
               non-deferred ``calculate_hook`` inside ``save()``) would double
               the work — recorded here as two invocations — turning every
               seeded calculation into twice the wall-clock it needs.
        """
        invocations: list = []
        loader_thread = threading.current_thread().name

        def _counting_calculate(inner_self):
            invocations.append(threading.current_thread().name)

        with patch.object(CelerySyncCalc, "calculate", _counting_calculate):
            self._run_seed_load(self._seed("seed-once"))

        self.assertEqual(
            len(invocations),
            1,
            "the seeded calculation body must execute EXACTLY once; "
            f"got {len(invocations)} invocations on threads {invocations!r} — "
            "more than one means the calc runs inline as well as on the pool "
            "(double work)",
        )
        self.assertTrue(
            invocations[0].startswith("lex-calc"),
            f"the single invocation must run on the lex-calc pool; got {invocations[0]!r}",
        )
        self.assertNotEqual(
            invocations[0],
            loader_thread,
            "the calculation must not run inline on the loader thread",
        )

    # ── 8.131 ────────────────────────────────────────────────────────
    def test_8_131_seed_dispatch_overhead_is_negligible(self) -> None:
        """
        Scenario 8.131: the wall-clock the seed path adds around a fixed-duration
        calculation is negligible versus running the same body bare on the pool.

        Given: a calculation body that sleeps a fixed, measurable ``SLEEP``.
        When:  we time (a) submitting that body straight onto
               ``_calculation_executor`` and blocking on it — the bare pool cost
               the live request path also pays — versus (b) driving it through
               the full seed loader (persist IN_PROGRESS + offload + block).
        Then:  the seed path's extra wall-clock (one persist + context copy) is
               under a generous 0.25s bound. The bound is deliberately below
               ``SLEEP`` (0.30s): the dominant "slowdown" regression — running
               the calc inline *and* on the pool, or serializing it behind a
               second run — would add roughly another full ``SLEEP``, which this
               bound catches, while the legitimate few-ms persist overhead
               passes comfortably.
        """
        SLEEP = 0.30
        OVERHEAD_BUDGET = 0.25  # < SLEEP, so a duplicate-run regression trips it

        def _sleeping_calculate(inner_self):
            time.sleep(SLEEP)

        # (a) Bare pool cost: submit the identical body straight to the pool and
        # block on it — exactly the per-calc work the request path performs.
        bare_instance = CelerySyncCalc(name="bare")
        with patch.object(CelerySyncCalc, "calculate", _sleeping_calculate):
            t0 = time.monotonic()
            _calculation_executor.submit(
                lambda: _sleeping_calculate(bare_instance)
            ).result(timeout=5)
            t_bare = time.monotonic() - t0

        # (b) Full seed path: persist IN_PROGRESS, offload the same body to the
        # pool, block on the future.
        with patch.object(CelerySyncCalc, "calculate", _sleeping_calculate):
            t0 = time.monotonic()
            self._run_seed_load(self._seed("seed-latency"))
            t_seed = time.monotonic() - t0

        overhead = t_seed - t_bare
        self.assertLess(
            overhead,
            OVERHEAD_BUDGET,
            f"the seed path added {overhead * 1000:.0f}ms over bare pool "
            f"execution of the same calc (bare={t_bare * 1000:.0f}ms, "
            f"seed={t_seed * 1000:.0f}ms); a regression that runs the calc "
            f"inline as well as on the pool would add ~{SLEEP * 1000:.0f}ms",
        )


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
