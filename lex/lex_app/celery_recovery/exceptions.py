"""Exceptions raised by the worker-recovery subsystem."""

from __future__ import annotations


class MaxRequeueExceeded(Exception):
    """Raised/recorded when a task's bounded retry budget is exhausted.

    The supervisor writes this as the task's FAILURE result so that any
    parent blocked on ``AsyncResult.get()`` unblocks instead of hanging
    forever, and so the failure reason is legible in the result backend.
    """

    def __init__(self, task_id: str, retries: int):
        self.task_id = task_id
        self.retries = retries
        super().__init__(
            f"Task {task_id} exceeded the recovery retry budget after {retries} requeues"
        )
