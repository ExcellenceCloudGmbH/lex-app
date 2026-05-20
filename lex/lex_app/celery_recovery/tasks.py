"""
Celery beat task that periodically invokes :func:`sweep_once`.

Registered in ``CELERY_BEAT_SCHEDULE`` with a schedule of
``LEX_TASK_SUPERVISOR_SCAN_INTERVAL`` seconds. Importing this module also
defines the task even if beat is not running, so an operator can invoke it
on-demand: ``celery -A lex_app call lex.lex_app.celery_recovery.tasks.sweep_dead_workers``.

This module also ships a tiny ``recovery_smoke_slow`` task that is
**only registered when LEX_RECOVERY_SMOKE_TASK=true**. It exists so a
human running the manual chaos recipe in
``docs/ci-cd/celery-worker-recovery.md`` always has a long-running task
to fire and SIGKILL — without it, every project would have to wire its
own. It is intentionally not registered by default so it can never leak
into a customer worker fleet.
"""
from __future__ import annotations

import logging
import os
import time

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="lex.lex_app.celery_recovery.tasks.sweep_dead_workers", ignore_result=True)
def sweep_dead_workers() -> dict:
    """Run one supervisor scan. Returns the summary dict for log enrichment."""
    from .supervisor import sweep_once

    summary = sweep_once()
    if summary["stale"]:
        logger.info("lex_recovery sweep summary=%s", summary)
    return summary


# ---------------------------------------------------------------------------
# Optional chaos-test helper. Off by default; opt in with
# ``LEX_RECOVERY_SMOKE_TASK=true`` (typically set on the local laptop, never
# in a customer environment).
# ---------------------------------------------------------------------------
if os.getenv("LEX_RECOVERY_SMOKE_TASK", "").strip().lower() in {"1", "true", "yes", "on"}:

    @shared_task(name="lex.lex_app.celery_recovery.tasks.recovery_smoke_slow")
    def recovery_smoke_slow(seconds: int = 60) -> str:
        """Sleep ``seconds`` then return a string. Used only for chaos testing.

        Intended workflow:

            # In one terminal:
            export LEX_RECOVERY_SMOKE_TASK=true
            lex celery worker -n victim@%h --concurrency=1

            # In another:
            export LEX_RECOVERY_SMOKE_TASK=true
            lex celery worker -n survivor@%h --concurrency=1

            # In a third:
            celery -A lex_app call \\
                lex.lex_app.celery_recovery.tasks.recovery_smoke_slow \\
                --args='[120]' --queue=celery

            # Then ``pkill -9 -f victim@`` and watch survivor's logs.

        The task body logs every 5 s so it's obvious in stdout when the
        worker is running it (vs. queued) — this matters because the
        SIGKILL must land while the task is in-flight, not while it's
        still in the broker queue.
        """
        deadline = time.monotonic() + max(0, int(seconds))
        emitted = 0
        while time.monotonic() < deadline:
            elapsed = int(seconds - (deadline - time.monotonic()))
            if elapsed >= emitted:
                logger.warning(
                    "recovery_smoke_slow still running, %ss elapsed of %ss",
                    elapsed, seconds,
                )
                emitted = elapsed + 5
            time.sleep(0.5)
        return f"recovery_smoke_slow done after {seconds}s"

    __all__ = ["sweep_dead_workers", "recovery_smoke_slow"]
else:
    __all__ = ["sweep_dead_workers"]


