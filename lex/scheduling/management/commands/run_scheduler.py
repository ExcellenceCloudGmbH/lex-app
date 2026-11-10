"""Run the scheduler's tick from a command, once or in a loop.

The backend runs the clock by itself (see ``lex.scheduling.loop``); this command
is for everything else:

    lex run_scheduler --once        # sync code-declared schedules, start what is due, wait for it
    lex run_scheduler               # run the clock in the foreground until stopped
    lex run_scheduler --interval 10

Running it next to a backend is safe: each due slot is claimed by exactly one
clock. ``--once`` waits for the runs it started, because a command that exits
takes its background runs with it.
"""

from __future__ import annotations

import signal
from concurrent.futures import wait

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start the scheduled jobs that are due. --once runs a single tick and waits for its runs."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one tick, wait for its runs, exit.")
        parser.add_argument(
            "--interval",
            type=float,
            default=None,
            help="Seconds between ticks (default: LEX_SCHEDULER_TICK_SECONDS).",
        )
        parser.add_argument(
            "--no-sync",
            action="store_true",
            help="Do not create/update the schedules declared with @lex_job(schedule=...).",
        )

    def handle(self, *args, **options):
        from lex.scheduling.dispatch import run_due_jobs
        from lex.scheduling.loop import SchedulerLoop, tick_seconds
        from lex.scheduling.registry import sync_code_jobs

        if options["once"]:
            if not options["no_sync"]:
                sync = sync_code_jobs()
                self.stdout.write(
                    f"Code-declared schedules: created={sync.created} updated={sync.updated} "
                    f"disabled={sync.disabled} conflicts={sync.conflicts}"
                )
            report = run_due_jobs()
            wait(report.futures)
            self.stdout.write(
                f"fired={report.fired} skipped={report.skipped} missed={report.missed} "
                f"failed_to_start={report.failed_to_start}"
            )
            return

        loop = SchedulerLoop(
            options["interval"] or tick_seconds(),
            sync=(lambda: None) if options["no_sync"] else None,
        )

        def _stop(signum, frame):  # noqa: ARG001 - signal handler signature
            loop.stop()

        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
        # Run in this thread: the command is the process, there is nothing else to serve.
        loop.run()
