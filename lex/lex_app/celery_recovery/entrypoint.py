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


def main() -> None:
    # Mirror the lex CLI / celery.py bootstrap: default the settings module if
    # the operator did not set it, then run django.setup() once.
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")

    import django

    django.setup()

    from django.core.management import call_command

    call_command("run_recovery_supervisor", *sys.argv[1:])


if __name__ == "__main__":
    main()
