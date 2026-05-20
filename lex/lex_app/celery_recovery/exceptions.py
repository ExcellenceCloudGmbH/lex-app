"""
Exception classes raised by the recovery system.

These are pickled into the Celery result backend by the supervisor via
``app.backend.mark_as_failure`` when a task's retry budget is exhausted. The
parent thread, blocked inside ``WaitForTasks.wait_for_completion`` on
``AsyncResult.get(propagate=True)``, re-raises them — so the parent
``CalculationModel`` can capture them like any other task failure.
"""
from __future__ import annotations


class WorkerLost(Exception):
    """A Celery worker died (or stopped heartbeating) while running this task.

    Carries the last known worker hostname and the attempt number on which the
    death was detected so audit/log output is useful without further lookup.
    """

    def __init__(
        self,
        message: str = "Celery worker stopped heartbeating",
        *,
        worker_hostname: str | None = None,
        attempt: int | None = None,
        task_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.worker_hostname = worker_hostname
        self.attempt = attempt
        self.task_id = task_id


class MaxRequeueExceeded(WorkerLost):
    """Raised when the supervisor has requeued a task ``max_retries`` times
    and the latest worker died again. Terminal — no further requeue.
    """


__all__ = ["WorkerLost", "MaxRequeueExceeded"]
