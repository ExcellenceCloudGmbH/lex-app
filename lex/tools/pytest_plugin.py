"""lex's pytest plugin: a plain pytest run is set up the way `lex pytest` sets one up.

PyCharm's runner (``_jb_pytest_runner.py``), VS Code's test explorer and a bare
``pytest`` call ``pytest.main()`` directly. Before this plugin none of `lex pytest`'s
setup happened for them, so their test trees showed one collection error ("settings
are not configured") instead of the tests, and projects copied the setup into a root
``conftest.py``. pytest loads this module through the ``pytest11`` entry point ``lex``;
``lex pytest`` passes it to ``pytest.main()`` itself. One implementation, two ways in.

It switches on only in a project with lex tests -- a ``lex_test_config.yaml`` at the
project root, or a ``$LEX_TEST_CONFIG_PAYLOAD`` -- and does nothing anywhere else.
``-p no:lex`` switches it off.

Like ``lex/tools/django_bootstrap.py``, this module imports nothing from Django or
``lex.lex_app`` at import time: the project's ``.env`` has to be loaded first.
"""

from __future__ import annotations

import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

CONFIG_FILENAME = "lex_test_config.yaml"
PAYLOAD_ENV_VAR = "LEX_TEST_CONFIG_PAYLOAD"

#: The name ``lexAdminSite.urls`` looks ``register_converter`` up under; see
#: :func:`_idempotent_register_converter`.
CONVERTER_LOOKUP = "lex.model_admin.sites.lex_admin_site.register_converter"


@dataclass
class _Run:
    """What this plugin set up for one pytest run, so that it undoes exactly that."""

    root: Path
    test_config: Any
    test_environment: bool = False
    db_runner: Any = None
    old_db_config: Any = None
    converter_patch: Any = None
    warning_filters: list = field(default_factory=list)


_RUN = pytest.StashKey[_Run]()


def project_root(invocation_dir: Path | str) -> Path:
    """The project a run serves, found the way the settings find it."""
    from lex.runtime_config import resolve_project_root

    return resolve_project_root(os.getenv("PROJECT_ROOT"), invocation_dir)


def is_lex_project(root: Path) -> bool:
    """Lex tests live here: a ``lex_test_config.yaml``, or a workflow's payload."""
    if (root / CONFIG_FILENAME).is_file():
        return True
    return bool(os.environ.get(PAYLOAD_ENV_VAR, "").strip())


@pytest.hookimpl(tryfirst=True)
def pytest_load_initial_conftests(early_config: pytest.Config) -> None:
    """The project's environment and ``django.setup()``, before any conftest loads.

    pytest imports the initial ``conftest.py`` files in its own, ``trylast``
    implementation of this hook, so a project conftest may import models.
    """
    root = project_root(early_config.invocation_params.dir)
    if not is_lex_project(root):
        return

    from lex.tools.django_bootstrap import prepare_environment

    prepare_environment(root)

    from lex.tools.test_groups import LexTestConfigError, resolve_config

    try:
        test_config = resolve_config(root)
    except LexTestConfigError as exc:
        raise pytest.UsageError(str(exc)) from exc

    # `lex pytest` runs from the project root, so paths relative to it resolve the same
    # in both runs. The test paths on the command line are resolved from the
    # invocation directory, which pytest recorded before this hook.
    os.chdir(root)

    import django

    filters_before = list(warnings.filters)
    django.setup()
    early_config.stash[_RUN] = _Run(
        root=root,
        test_config=test_config,
        warning_filters=[f for f in warnings.filters if f not in filters_before],
    )


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """The groups as markers; the test environment and database, unless already set up.

    ``tryfirst`` so that this runs before any conftest's ``pytest_configure``: a
    project conftest that copied this setup and checks whether it already happened
    finds it done and stands down.
    """
    run = config.stash.get(_RUN, None)
    if run is None:
        return

    _reinstall_warning_filters(run.warning_filters)

    if config.pluginmanager.hasplugin("django"):
        raise pytest.UsageError(
            "pytest-django is active as well as lex's pytest plugin. Both set up "
            "Django's test environment, and the second attempt fails. Add "
            "`-p no:django` to your pytest options, or uninstall pytest-django."
        )

    from lex.tools.test_groups import LexGroupsPlugin

    if not any(isinstance(plugin, LexGroupsPlugin) for plugin in config.pluginmanager.get_plugins()):
        # `lex pytest` registers its own, whose results feed the report.
        config.pluginmanager.register(LexGroupsPlugin(run.test_config, strict=True), "lex-groups")

    if config.option.collectonly:
        return

    from django.test.utils import _TestState

    if hasattr(_TestState, "saved_data"):
        # Set up before this hook ran -- by a project conftest that copied this setup,
        # or by the process this run is nested in. A second setup_test_environment()
        # raises, so it stays theirs, teardown included.
        return

    _set_up_test_run(run)


def _reinstall_warning_filters(filters: list) -> None:
    """Put back the warning filters ``django.setup()`` installed.

    Importing the settings installs lex's warning policy (``LEX_SUPPRESS_WARNINGS``).
    pytest runs ``pytest_load_initial_conftests`` inside a ``warnings.catch_warnings()``
    block, so filters installed there are dropped when the hook returns. `lex pytest`
    imported the settings before pytest started, and its tests ran under them;
    reinstalled here, outside that block, both runs report the same warnings.
    """
    for action, message, category, module, lineno in reversed(filters):
        warnings.filterwarnings(
            action,
            message=message.pattern if message is not None else "",
            category=category,
            module=module.pattern if module is not None else "",
            lineno=lineno,
        )


def _set_up_test_run(run: _Run) -> None:
    """``setup_test_environment()``, the test database, the converter patch."""
    from django.test.runner import DiscoverRunner
    from django.test.utils import setup_test_environment, teardown_test_environment

    setup_test_environment()
    run.test_environment = True

    # keepdb=True on purpose: deployed settings set TEST["NAME"] to the instance's own
    # database, so there the "test database" exists already. keepdb reuses it, needs
    # no CREATEDB privilege, and never drops the instance's data. Only the default
    # alias: GCP / K8S / DOCKER-COMPOSE point at hosts that are unset locally, and
    # Django would try to create a test database on each.
    db_runner = DiscoverRunner(verbosity=1, interactive=False, keepdb=True)
    try:
        run.old_db_config = db_runner.setup_databases(aliases={"default"})
    except Exception as exc:
        teardown_test_environment()
        run.test_environment = False
        raise pytest.UsageError(f"Could not set up the test database: {exc}") from exc
    run.db_runner = db_runner

    run.converter_patch = _idempotent_register_converter()
    run.converter_patch.start()


def _idempotent_register_converter():
    """A patch that lets ``lexAdminSite.urls`` register its converter again.

    ``_get_urls`` calls ``register_converter(..., "model")`` each time the URLs are
    built, and Django raises "Converter 'model' is already registered" the second
    time. One process running every cluster hits that as soon as a test reloads
    ``lex_app.urls``.
    """
    from unittest.mock import patch

    from django.urls import converters
    from django.urls.converters import REGISTERED_CONVERTERS

    register_converter = converters.register_converter

    def register_again(converter, type_name):
        REGISTERED_CONVERTERS.pop(type_name, None)
        return register_converter(converter, type_name)

    return patch(CONVERTER_LOOKUP, new=register_again)


@pytest.hookimpl(trylast=True)
def pytest_unconfigure(config: pytest.Config) -> None:
    """Undo, in reverse, what :func:`pytest_configure` set up -- and nothing else."""
    run = config.stash.get(_RUN, None)
    if run is None:
        return
    if run.converter_patch is not None:
        run.converter_patch.stop()
        run.converter_patch = None
    if run.db_runner is not None:
        # keepdb: restores the connection settings and drops nothing.
        run.db_runner.teardown_databases(run.old_db_config)
        run.db_runner = None
    if run.test_environment:
        from django.test.utils import teardown_test_environment

        teardown_test_environment()
        run.test_environment = False
