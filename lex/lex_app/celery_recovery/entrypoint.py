"""Console-script entrypoints for the worker-recovery drivers.

``lex-recovery-supervisor`` runs the always-on supervisor loop (the legacy /
default cluster driver and the local fallback). ``lex-recovery-beat`` runs the
embedded-beat self-consuming worker that fires the recovery sweep on a schedule
visible in the Django admin (django_celery_beat DatabaseScheduler) while
consuming ONLY the dedicated ``recovery`` queue, so recovered work re-dispatched
by ``_requeue`` flows to the main queue and drives KEDA, never looping back here.

Both bootstrap Django exactly like the lex CLI (see ``lex/bin/lex.py`` and
``lex/lex_app/celery.py``) before importing app code.

Usage:

    lex-recovery-supervisor              # always-on supervisor loop
    lex-recovery-supervisor --once       # single pass and exit
    lex-recovery-beat                    # embedded-beat recovery worker
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import List, Optional


def _bootstrap_django() -> None:
    # Mirror the lex CLI bootstrap (see lex/bin/lex.py). The framework's modules
    # import each other as *top-level* packages (e.g. ``from core.models...``,
    # ``lex_app.settings``), which only resolves when the inner ``lex/`` package
    # directory is on sys.path. Launching a console script bypasses the CLI, so
    # we replicate it or django.setup() dies with ModuleNotFoundError while
    # populating apps. ``parents[2]`` of this file is that inner ``lex/`` dir.
    package_root = Path(__file__).resolve().parents[2].as_posix()
    if package_root not in sys.path:
        sys.path.append(package_root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", package_root)

    import django

    django.setup()


def main() -> None:
    _bootstrap_django()

    from django.core.management import call_command

    call_command("run_recovery_supervisor", *sys.argv[1:])


def beat_main(argv: Optional[List[str]] = None) -> None:
    """Launch an embedded-beat worker bound to the 'recovery' queue.

    Obtains the Celery app via ``supervisor._get_app()`` — the same configured
    app the supervisor and ``_requeue`` use — so there is exactly one app
    object, and launches via ``app.worker_main`` to avoid fragile ``-A``
    discovery (the worker image's ``celery -A`` target is not in this repo).
    Concurrency 1: the only work on this queue is the lightweight sweep; the
    heavy recovered tasks run on the autoscaled main-queue workers, not here.
    """
    _bootstrap_django()

    from lex.lex_app.celery_recovery import supervisor

    app = supervisor._get_app()

    worker_argv = [
        "worker",
        "-B",
        "-Q",
        "recovery",
        "--concurrency",
        "1",
        "--scheduler",
        "django_celery_beat.schedulers:DatabaseScheduler",
        "-l",
        "info",
        *(argv if argv is not None else sys.argv[1:]),
    ]
    app.worker_main(worker_argv)


if __name__ == "__main__":
    main()
