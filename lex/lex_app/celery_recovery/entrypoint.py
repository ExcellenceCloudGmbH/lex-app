"""Console-script entrypoint for the worker-recovery supervisor.

Exposed via ``[project.scripts]`` as ``lex-recovery-supervisor`` so the
supervisor can be launched as a single command in a container without going
through the full ``lex`` CLI. It bootstraps Django exactly like the lex CLI
does (see ``lex/bin/lex.py`` and ``lex/lex_app/celery.py``) and then forwards
to the ``run_recovery_supervisor`` management command, passing through any
CLI args (e.g. ``--once``, ``--interval``).

Usage:

    lex-recovery-supervisor              # always-on loop
    lex-recovery-supervisor --once       # single pass and exit
    lex-recovery-supervisor --interval 5
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    # Mirror the lex CLI bootstrap (see lex/bin/lex.py). The framework's modules
    # import each other as *top-level* packages (e.g. ``from core.models...``,
    # ``lex_app.settings``), which only resolves when the inner ``lex/`` package
    # directory is on sys.path. The lex CLI appends it; launching this console
    # script goes around the CLI, so we must replicate it here or django.setup()
    # dies with ``ModuleNotFoundError: No module named 'core'`` while populating
    # apps. ``parents[2]`` of this file is that inner ``lex/`` directory.
    package_root = Path(__file__).resolve().parents[2].as_posix()
    if package_root not in sys.path:
        sys.path.append(package_root)

    # Default the settings module if the operator did not set it, mirroring the
    # CLI / celery.py, then run django.setup() once.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", package_root)

    import django

    django.setup()

    from django.core.management import call_command

    call_command("run_recovery_supervisor", *sys.argv[1:])


if __name__ == "__main__":
    main()
