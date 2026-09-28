"""The ``lex`` CLI's Django bootstrap, for processes the CLI does not start.

``lex streamlit`` runs Streamlit inside the CLI process, after the CLI has set
Django up -- so a Streamlit dashboard imports a model and queries it with
nothing further to do. Reflex cannot be served that way. ``reflex run``
compiles the app in one worker process and serves it from others (granian
workers, re-spawned on every hot reload), and none of them runs the CLI's
bootstrap. The Reflex app module calls :func:`setup_django` instead, before
anything imports a model.

This module lives outside ``lex.lex_app`` on purpose, and must stay that way:
importing anything under ``lex.lex_app`` evaluates Django's settings (the
Celery app validates its broker configuration at import), and the settings read
the environment -- so the project's ``.env`` has to be loaded *before* that
first import, which only a module outside the package can do.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from lex.tools.project_root import find_project_root, load_project_env_file

#: The directory holding the ``lex`` package's own top-level modules. The CLI
#: puts the same directory on ``sys.path`` as ``LEX_APP_PACKAGE_ROOT``, and it is
#: what makes ``DJANGO_SETTINGS_MODULE=lex_app.settings`` importable at all.
LEX_PACKAGE_DIR = Path(__file__).resolve().parents[1]


def project_root() -> Path:
    """The project this process serves: ``PROJECT_ROOT`` if set, else from the cwd.

    ``lex`` sets ``PROJECT_ROOT`` before anything else runs, and every process
    it starts inherits it. The fallback is what the CLI itself computes, so a
    bare ``reflex run`` in the project root resolves the same directory.
    """
    configured = os.getenv("PROJECT_ROOT")
    if configured:
        return Path(configured).resolve()
    return Path(find_project_root(os.getcwd())).resolve()


def prepare_environment() -> Path:
    """Give this process the environment ``lex/bin/lex.py`` gives its own.

    The same steps in the same order: the project's ``.env`` (never overriding
    a variable the process already has), the settings module,
    ``PROJECT_ROOT``, and the package directory on ``sys.path``. Safe to call
    any number of times. Returns the project root.
    """
    root = project_root()
    load_project_env_file(root)
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "lex_app.settings")
    os.environ.setdefault("PROJECT_ROOT", root.as_posix())
    os.environ.setdefault("LEX_APP_PACKAGE_ROOT", LEX_PACKAGE_DIR.as_posix())

    package_dir = LEX_PACKAGE_DIR.as_posix()
    if package_dir not in sys.path and str(LEX_PACKAGE_DIR) not in sys.path:
        sys.path.append(package_dir)
    return root


def setup_django() -> None:
    """:func:`prepare_environment`, then ``django.setup()`` -- once per process.

    A process that already set Django up -- ``lex pytest``, or a worker that
    imported the app module before -- returns immediately.
    """
    from django.apps import apps

    if apps.ready:
        return

    prepare_environment()

    import django

    django.setup()
