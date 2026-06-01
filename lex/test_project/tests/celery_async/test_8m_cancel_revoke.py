"""Cluster 8m: cancellation-aware CallbackTask failure mapping.

Intent
------
When the abort button revokes a Celery task, the worker process raises
one of a small set of cancellation exceptions (``TaskRevokedError``,
``SoftTimeLimitExceeded``, ``WorkerLostError``, ``billiard.Terminated``,
or the framework's own ``CalculationCancelled`` marker). The
``CallbackTask.on_failure`` hook must persist ``is_calculated=ABORTED``
for those — not ``ERROR`` — so the user-facing state matches what
actually happened (the customer pressed cancel; the calculation did
not fail with a bug).

A regression here would silently flip every aborted calc to ``ERROR``,
which would page the on-call team every time someone clicks the abort
button — exactly the wrong incident signal.

Cluster 8m — scenarios 8.58–8.61. Type: U (SimpleTestCase — exercises
``_is_cancellation_exception`` against a synthetic exception hierarchy
mirroring celery/billiard's class names without importing the real
worker stack).
Covers: ``lex/lex_app/celery_tasks.py`` — ``_is_cancellation_exception``
and the ``CallbackTask.on_failure`` branch that consumes it.
Run: python -m lex pytest lex/test_project/tests/celery_async/test_8m_cancel_revoke.py -v
"""

from __future__ import annotations

import pytest
from django.test import SimpleTestCase

from lex.core.models.CalculationModel import CalculationCancelled
from lex.lex_app.celery_tasks import _is_cancellation_exception

pytestmark = pytest.mark.celery_async


# Synthetic stand-ins for the celery/billiard exception classes. We
# match on class *name* in _is_cancellation_exception so we don't have
# to hard-import the celery/billiard worker modules (the import surface
# differs between celery 4/5 and billiard versions); the helper walks
# ``type(exc).__mro__`` looking for any class named in the cancellation
# allow-list. Defining stand-ins here proves the contract end-to-end
# without smuggling worker-internal imports into the test runtime.
class TaskRevokedError(Exception):
    """Stand-in mirroring ``celery.exceptions.TaskRevokedError``."""


class SoftTimeLimitExceeded(Exception):
    """Stand-in mirroring ``celery.exceptions.SoftTimeLimitExceeded``."""


class WorkerLostError(Exception):
    """Stand-in mirroring ``billiard.exceptions.WorkerLostError``."""


class Terminated(Exception):
    """Stand-in mirroring ``billiard.exceptions.Terminated``."""


class TestCluster08m_CancellationExceptionDetector(SimpleTestCase):
    """Cluster 8m: ``_is_cancellation_exception`` mapping."""

    # ------------------------------------------------------------------
    # 8.58 — every documented cancellation class is recognised
    # ------------------------------------------------------------------
    def test_08_58_recognises_celery_and_billiard_cancellation_classes(self):
        """
        Scenario 8.58: every name in the cancellation allow-list is recognised.
        Given: instances of TaskRevokedError, SoftTimeLimitExceeded,
               WorkerLostError, Terminated, CalculationCancelled.
        When:  _is_cancellation_exception(exc) is called for each.
        Then:  every call returns True. None of the user's calculations
               are silently flipped to ERROR after a revoke.
        """
        for exc in (
            TaskRevokedError("revoked"),
            SoftTimeLimitExceeded("soft limit"),
            WorkerLostError("worker lost"),
            Terminated("term"),
            CalculationCancelled("user cancel"),
        ):
            with self.subTest(exc_class=type(exc).__name__):
                self.assertTrue(
                    _is_cancellation_exception(exc),
                    msg=(
                        f"{type(exc).__name__} must be recognised as a "
                        "cancellation; otherwise on_failure persists ERROR"
                    ),
                )

    # ------------------------------------------------------------------
    # 8.59 — generic runtime errors are NOT cancellations
    # ------------------------------------------------------------------
    def test_08_59_generic_runtime_errors_are_not_cancellations(self):
        """
        Scenario 8.59: unrelated exceptions stay on the ERROR path.
        Given: a RuntimeError raised by user calculate() code.
        When:  _is_cancellation_exception(exc) is called.
        Then:  False — these are real bugs and must persist as ERROR
               so the audit row shows the real failure.
        """
        for exc in (
            RuntimeError("kaboom"),
            ValueError("bad input"),
            KeyError("missing"),
            ZeroDivisionError("/ by 0"),
        ):
            with self.subTest(exc_class=type(exc).__name__):
                self.assertFalse(
                    _is_cancellation_exception(exc),
                    msg=(
                        f"{type(exc).__name__} must NOT be flagged as "
                        "cancellation; that would hide real failures"
                    ),
                )

    # ------------------------------------------------------------------
    # 8.60 — subclasses of cancellation classes are also recognised
    # ------------------------------------------------------------------
    def test_08_60_subclasses_of_cancellation_classes_are_recognised(self):
        """
        Scenario 8.60: the detector walks the MRO so subclasses count too.
        Given: a custom subclass of TaskRevokedError.
        When:  _is_cancellation_exception is called on an instance.
        Then:  True — robustness against frameworks/middleware that
               wrap revoked errors in their own subclass.
        """

        class CustomRevoked(TaskRevokedError):
            pass

        self.assertTrue(_is_cancellation_exception(CustomRevoked("wrapped")))

    # ------------------------------------------------------------------
    # 8.61 — None / non-exception input is safe (returns False)
    # ------------------------------------------------------------------
    def test_08_61_none_returns_false_safely(self):
        """
        Scenario 8.61: defensive guard for the on_failure code path.
        Given: ``None`` (celery occasionally passes None for exc on
               obscure retry paths).
        When:  _is_cancellation_exception(None) is called.
        Then:  False, no AttributeError — the callback continues into
               the ERROR persistence branch which is the safer default.
        """
        self.assertFalse(_is_cancellation_exception(None))

