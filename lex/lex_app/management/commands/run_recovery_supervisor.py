"""Always-on Celery worker-recovery supervisor (management command).

Runs the recovery sweep loop as a single, dedicated, always-on process. In
the cluster the backend (KEDA ScaledObject, 0..1) and the Celery workers
(KEDA ScaledJob) both scale to zero, so neither can reliably host the loop —
this command exists to be deployed as a singleton companion pod that just
keeps calling :func:`scan_and_recover` forever.

Each pass detects tasks whose worker died (expired heartbeat) and either
re-dispatches them (within the retry budget) or finalizes them ABORTED. See
``lex/lex_app/celery_recovery/supervisor.py`` for the recovery semantics.

Run it:

    # always-on loop (the deployed form)
    manage.py run_recovery_supervisor
    lex run_recovery_supervisor

    # single pass and exit (cron / debugging)
    manage.py run_recovery_supervisor --once

Gating: recovery only does real work when ``CELERY_ACTIVE`` and
``LEX_TASK_RECOVERY_ENABLED`` are both on. If they are off the supervisor
still starts (so a misconfiguration is loud, not a silent exit) but every
pass is an inert no-op.
"""

from __future__ import annotations

import logging
import signal

from django.conf import settings
from django.core.management.base import BaseCommand

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Run the always-on Celery worker-recovery supervisor loop "
        "(detects dead workers and requeues their in-flight tasks). "
        "Use --once for a single sweep."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--interval",
            type=int,
            default=None,
            help=(
                "Seconds between recovery sweeps. Defaults to the "
                "LEX_TASK_SUPERVISOR_SCAN_INTERVAL setting (10s)."
            ),
        )
        parser.add_argument(
            "--once",
            action="store_true",
            help="Run a single scan_and_recover() pass and exit (cron/debugging).",
        )

    def handle(self, *args, **options):
        # Import inside handle so Django is fully set up first and so the
        # command module stays import-light for `manage.py` discovery.
        from lex.lex_app.celery import app
        from lex.lex_app.celery_recovery import enable, supervisor

        interval = options["interval"]
        run_once = options["once"]

        celery_active = bool(getattr(settings, "CELERY_ACTIVE", False))
        recovery_enabled = bool(getattr(settings, "LEX_TASK_RECOVERY_ENABLED", True))

        self.stdout.write(
            "Starting Celery worker-recovery supervisor "
            f"(CELERY_ACTIVE={celery_active}, "
            f"LEX_TASK_RECOVERY_ENABLED={recovery_enabled}, "
            f"interval={interval if interval is not None else 'settings-default'}, "
            f"once={run_once})"
        )
        logger.info(
            "run_recovery_supervisor starting: CELERY_ACTIVE=%s "
            "LEX_TASK_RECOVERY_ENABLED=%s interval=%s once=%s",
            celery_active,
            recovery_enabled,
            interval,
            run_once,
        )

        if not (celery_active and recovery_enabled):
            warning = (
                "Recovery is DISABLED (CELERY_ACTIVE and/or "
                "LEX_TASK_RECOVERY_ENABLED are off). The supervisor will run "
                "but every sweep is an inert no-op. Set CELERY_ACTIVE=TRUE and "
                "LEX_TASK_RECOVERY_ENABLED=true to activate recovery."
            )
            self.stdout.write(self.style.WARNING(warning))
            logger.warning(warning)

        # Harmless on the supervisor (consumer-side) process; just ensures the
        # recovery package is initialized. enable() is gated/idempotent.
        try:
            enable(app)
        except Exception:  # pragma: no cover - never block startup on wiring
            logger.exception(
                "run_recovery_supervisor: enable(app) failed; continuing"
            )

        if run_once:
            stats = supervisor.scan_and_recover(app)
            self.stdout.write(f"Recovery sweep stats: {stats}")
            logger.info("run_recovery_supervisor --once complete: %s", stats)
            return

        # Graceful shutdown: K8s sends SIGTERM on pod termination. Install
        # handlers that ask the loop to exit after the current pass finishes.
        def _request_stop(signum, _frame):
            logger.info(
                "run_recovery_supervisor received signal %s; stopping loop", signum
            )
            supervisor.stop_forever()

        signal.signal(signal.SIGTERM, _request_stop)
        signal.signal(signal.SIGINT, _request_stop)

        # run_forever already catches per-pass exceptions internally; this
        # wrapper is just a final safety net so any unexpected failure exits
        # non-zero and lets K8s restart the pod.
        try:
            supervisor.run_forever(interval)
        except Exception:
            logger.exception(
                "run_recovery_supervisor: supervisor loop crashed; exiting non-zero"
            )
            raise

        self.stdout.write("Celery worker-recovery supervisor stopped cleanly.")
        logger.info("run_recovery_supervisor stopped cleanly.")
