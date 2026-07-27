"""
Cluster 15 models — Async Calculation Dispatch & ASGI Responsiveness.

Per the test-plan rule "per-cluster models, never reused" (see
``test-clusters.md`` § Testing Philosophy), this module defines the
calculation models the asgi-responsiveness tests need.  The shape is
deliberately minimal — the cluster is about *when* the calculation
runs and *who else* can do work while it does, not about what the
calculation computes.
"""

from __future__ import annotations

import os
import threading
import time

from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 15")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 15")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class SlowCalc(CalculationModel):
    """A calculation that takes a known, measurable amount of time.

    The async-202 contract (feature #4) says the PATCH must return
    *before* the calculation completes.  To verify that, the test
    needs a calc whose duration is long enough to measure but short
    enough to keep the suite fast.  ``sleep_seconds`` defaults to
    0.5 s — comfortably longer than the few-ms window the request
    handler should take, comfortably shorter than the suite's
    timeout budget.

    The thread name and timestamp are captured inside ``calculate()``
    so feature #1 tests (dedicated calc thread pool) can assert that
    the calc ran on a ``lex-calc-*`` worker, not on asgiref's
    single-thread executor.
    """

    name = models.CharField(max_length=200)
    sleep_seconds = models.FloatField(default=0.5)
    # Set inside ``calculate()`` — observed by tests via
    # ``refresh_from_db``.  Stored on the row so cross-thread
    # visibility works the same way the real frontend's polling
    # would observe it.
    calc_thread_name = models.CharField(max_length=200, blank=True, default="")
    calc_started_at_monotonic = models.FloatField(null=True, blank=True)
    calc_finished_at_monotonic = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        # Capture the thread we're actually running on.  This is the
        # observable signal that the dedicated calc pool is in use —
        # asgiref's single_thread_executor names its workers
        # ``ThreadPoolExecutor-<n>_0``; the lex pool prefixes with
        # ``lex-calc``.
        self.calc_thread_name = threading.current_thread().name
        self.calc_started_at_monotonic = time.monotonic()
        time.sleep(self.sleep_seconds)
        self.calc_finished_at_monotonic = time.monotonic()


@_permissive
class ConcurrencyProbeCalc(CalculationModel):
    """Records its start/end monotonic timestamps so concurrent runs
    can be compared.

    Used by the "ASGI thread stays free during a long calc" test:
    we kick off a slow calc on one record and a fast probe on
    another, and assert their lifetimes overlap.  If the dedicated
    calc pool is wired correctly the two runs share wall-clock time;
    if calculations were back on the asgiref single-thread executor,
    the second would queue behind the first."""

    name = models.CharField(max_length=200)
    sleep_seconds = models.FloatField(default=0.2)
    started_at_monotonic = models.FloatField(null=True, blank=True)
    finished_at_monotonic = models.FloatField(null=True, blank=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        self.started_at_monotonic = time.monotonic()
        time.sleep(self.sleep_seconds)
        self.finished_at_monotonic = time.monotonic()


ALL_MODELS = [SlowCalc, ConcurrencyProbeCalc]

SLOW_CALC = "slowcalc"
CONCURRENCY_PROBE = "concurrencyprobecalc"


# Re-exported for tests that need to read the calc-thread env-var
# contract without re-importing the framework module.  Keeping the
# string here means a typo in the env-var name in one test won't
# diverge silently from the framework's own reader.
LEX_CALCULATION_THREADS_ENV = "LEX_CALCULATION_THREADS"
ASGI_THREADS_ENV = "ASGI_THREADS"

# Documented defaults (see Melih's commit bde8dde, items #1 and #3).
LEX_CALCULATION_THREADS_DEFAULT = 10
ASGI_THREADS_DEFAULT = 3

