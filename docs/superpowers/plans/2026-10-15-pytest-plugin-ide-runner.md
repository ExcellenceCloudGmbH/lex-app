# lex pytest plugin for IDE test runners — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A plain pytest run in a lex project (PyCharm's runner, VS Code, a terminal) gets the same setup `lex pytest` does, from one shared pytest plugin, and `lex setup` writes ready test run configurations.

**Architecture:** A new module `lex/tools/pytest_plugin.py`, registered as the `pytest11` entry point `lex`, does `lex pytest`'s setup in pytest hooks: environment and `django.setup()` in `pytest_load_initial_conftests`, groups / test environment / test database / converter patch in `pytest_configure`, teardown in `pytest_unconfigure`. `lex pytest` and `lex pytest-groups` drop their own copy and pass the module to `pytest.main()` with `-p no:lex`. `generate_pycharm_configs.py` adds PyCharm `tests`/`py.test` configurations and VS Code `pytest` launch entries when `lex_test_config.yaml` loads.

**Tech Stack:** Python 3.12, pytest 9 (hooks, `pytest.Stash`), Django test runner, click, xml.sax.saxutils.

**Spec:** `docs/superpowers/specs/2026-10-15-pytest-plugin-ide-runner-design.md`

## Global Constraints

- Python `>=3.12,<3.13` (pyproject `requires-python`).
- The plugin module imports nothing from Django or `lex.lex_app` at import time.
- Entry point exactly: `[project.entry-points.pytest11]` `lex = "lex.tools.pytest_plugin"`; opt-out flag `-p no:lex`.
- Test plan: cluster 1, letter **aw**, scenarios **1.393 – 1.404**, file `lex/test_project/tests/init/test_1aw_pytest_plugin.py`, classes `TestCluster01aw_*`, module marker `pytest.mark.init`. (Letter `av` / 1.386–1.392 belongs to open PR #810.)
- No customer project names, code or data in any file, commit, PR or docs page. Refer to "a project's copied conftest".
- No issue IDs in branch names, PR titles or bodies.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`; PR bodies end with `🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- This branch changes a workflow file: push with `git push git@github.com:ExcellenceCloudGmbH/lex-app.git feat/pytest-plugin-ide-runner` (the gh token lacks `workflow` scope).
- One local test run at a time (shared Postgres test database). Local command, from the worktree root:
  `DJANGO_SETTINGS_MODULE=lex_app.settings DATABASE_DEPLOYMENT_TARGET=default CELERY_ACTIVE=False /home/syscall/Documents/lex/.venv-test/bin/python -m lex pytest <paths> -p no:cacheprovider`
  Abbreviated below as `$LEXPYTEST <paths>`.
- Never hand-edit `lex/test_project/test-plan/progress/dashboard.md`; regenerate it.

## File structure

| File | Responsibility |
|---|---|
| `lex/tools/pytest_plugin.py` (new) | Activation rule, the three hooks, the converter patch |
| `lex/tools/django_bootstrap.py` | `prepare_environment(root=None)` accepts the root the plugin resolved |
| `pyproject.toml` | the `pytest11` entry point |
| `lex/bin/lex.py` | `pytest_cmd` loses steps 2–5 and passes the plugin module |
| `lex/tools/test_groups.py` | `collect_groups` passes the plugin module |
| `generate_pycharm_configs.py` | test run configurations for PyCharm and VS Code |
| `.github/scripts/pytest.ini` (new) | keeps the CI-helper tests plain: `-p no:lex -p no:django` |
| `.github/workflows/scripts_tests.yml` | comments now name that ini |
| `lex/lex_app/management/commands/tests/test_lex_pytest_report_flags.py` | the new `pytest.main()` call shape |
| `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (new) | scenarios 1.393–1.404 |
| `lex/test_project/tests/init/pytest_plugin_targets/` (new) | modules the tests run in a child `python -m pytest`; no `test_` prefix, so the suite never collects them |
| `lex/test_project/test-plan/clusters/01-init/{allocation.yaml,batches.md,cluster.md}`, `progress/sessions/2026-10-15-1aw-pytest-plugin.md`, `progress/dashboard.md` | test-plan sync |

---

### Task 1: The plugin sets a plain pytest run up

**Files:**
- Create: `lex/tools/pytest_plugin.py`
- Modify: `lex/tools/django_bootstrap.py:45-62` (`prepare_environment`)
- Modify: `pyproject.toml` (after `[project.scripts]`)
- Create: `lex/test_project/tests/init/pytest_plugin_targets/__init__.py`, `.../django_db_target.py`
- Test: `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (scenarios 1.393–1.396)

**Interfaces:**
- Produces: `lex.tools.pytest_plugin.project_root(invocation_dir) -> Path`, `is_lex_project(root) -> bool`, `_Run` dataclass (`root`, `test_config`, `test_environment: bool`, `db_runner`, `old_db_config`, `converter_patch`), `_RUN: pytest.StashKey[_Run]`, hooks `pytest_load_initial_conftests`, `pytest_configure`, `pytest_unconfigure`.
- Produces: `lex.tools.django_bootstrap.prepare_environment(root: Path | str | None = None) -> Path`.
- Produces (tests): `_run_pytest(*args, cwd=REPO_ROOT, extra_env=None) -> subprocess.CompletedProcess`, `_declared_entry_point() -> str`, `TARGETS_REL: str`.

- [ ] **Step 1: Write the target module the child run executes**

`lex/test_project/tests/init/pytest_plugin_targets/__init__.py`:

```python
"""Modules test_1aw_pytest_plugin.py runs in a child `python -m pytest`.

None is named ``test_*``, so the suite never collects them; a child run names each
file explicitly, which pytest collects whatever its name.
"""
```

`lex/test_project/tests/init/pytest_plugin_targets/django_db_target.py`:

```python
"""Needs everything `lex pytest` sets up: settings, Django, the test database.

The model import at module level fails at collection unless Django is set up before
the module is imported -- the "settings are not configured" error PyCharm showed.
"""

import os

import pytest
from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase

pytestmark = pytest.mark.init


class DjangoAndTestDatabase(TestCase):
    def test_settings_and_test_database(self):
        self.assertEqual(os.environ.get("DJANGO_SETTINGS_MODULE"), "lex_app.settings")
        self.assertTrue(
            connection.settings_dict["NAME"].startswith("test_"),
            f"expected the test database, got {connection.settings_dict['NAME']!r}",
        )
        User.objects.create(username="pytest-plugin-target")
        self.assertTrue(User.objects.filter(username="pytest-plugin-target").exists())
```

- [ ] **Step 2: Write the failing tests (1.393–1.396)**

`lex/test_project/tests/init/test_1aw_pytest_plugin.py`:

```python
"""A plain pytest run -- PyCharm's, VS Code's, a terminal's -- is set up like `lex pytest`.

Intent: PyCharm's runner calls pytest.main() directly, so none of `lex pytest`'s
setup happened and its test tree showed one collection error ("settings are not
configured") instead of the tests. lex-app ships a pytest plugin, loaded through the
`pytest11` entry point `lex`, that does that setup in any project with a
lex_test_config.yaml, and `lex pytest` runs on the same plugin. A regression puts the
collection error back in every IDE, or lets a plain run and `lex pytest` disagree.
Cluster 1aw -- scenarios 1.393-1.404. Type: U / I.
Covers: lex/tools/pytest_plugin.py, lex/tools/django_bootstrap.py (prepare_environment),
lex/bin/lex.py (pytest_cmd), lex/tools/test_groups.py (collect_groups),
generate_pycharm_configs.py (test run configurations), pyproject.toml (the entry point).
Run: python -m lex pytest lex/test_project/tests/init/test_1aw_pytest_plugin.py -v
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

import pytest

from lex.tools import django_bootstrap, pytest_plugin

pytestmark = pytest.mark.init

REPO_ROOT = Path(__file__).resolve().parents[4]
TARGETS = Path(__file__).resolve().parent / "pytest_plugin_targets"
TARGETS_REL = TARGETS.relative_to(REPO_ROOT).as_posix()

# What `lex pytest`, which runs this suite, put in the environment. An IDE's process
# starts without it, so the child runs do too.
_SETUP_VARS = (
    "DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "LEX_APP_PACKAGE_ROOT",
    "LEX_TEST_CONFIG_PAYLOAD", "PYTEST_ADDOPTS", "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
)


def _declared_entry_point() -> str:
    """The module pyproject.toml registers as the `lex` pytest plugin."""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["entry-points"]["pytest11"]["lex"]


def _entry_point_dir(tmp: Path) -> Path:
    """A directory that registers the plugin, on PYTHONPATH, the way an install does.

    The test venv may hold an editable install made before the entry point existed.
    importlib.metadata finds this dist-info as well, so the child loads the plugin by
    pytest's autoload -- the path PyCharm takes -- whatever happens to be installed.
    """
    dist = tmp / "lex_pytest_entry_point-0.dist-info"
    dist.mkdir(parents=True)
    (dist / "METADATA").write_text(
        "Metadata-Version: 2.1\nName: lex-pytest-entry-point\nVersion: 0\n", encoding="utf-8"
    )
    (dist / "entry_points.txt").write_text(
        f"[pytest11]\nlex = {_declared_entry_point()}\n", encoding="utf-8"
    )
    return tmp


def _run_pytest(*args: str, cwd: Path = REPO_ROOT, extra_env: dict | None = None):
    """`python -m pytest ARGS` the way an IDE starts it: no `lex` CLI, none of its env."""
    with tempfile.TemporaryDirectory() as tmp:
        env = {key: value for key, value in os.environ.items() if key not in _SETUP_VARS}
        env["PYTHONPATH"] = os.pathsep.join([str(_entry_point_dir(Path(tmp))), str(REPO_ROOT)])
        env.update(extra_env or {})
        return subprocess.run(
            [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
            cwd=cwd, env=env, capture_output=True, text=True, timeout=600, check=False,
        )


def _output(result: subprocess.CompletedProcess) -> str:
    return f"exit {result.returncode}\nSTDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"


class TestCluster01aw_PlainRunIsSetUp(TestCase):
    """Cluster 1aw: a plain pytest run gets `lex pytest`'s setup from the plugin."""

    # -- 1.393 ---------------------------------------------------------
    def test_1_393_a_plain_run_gets_django_and_the_test_database(self):
        """
        Scenario 1.393: the PyCharm way
        Given: a lex project (this repository has a lex_test_config.yaml)
        When: `python -m pytest <file>` runs a test that imports a model at module
              level and writes to the database, with none of `lex pytest`'s environment
        Then: it passes -- the plugin loaded through the entry point set Django and the
              test database up before collection
        """
        result = _run_pytest(f"{TARGETS_REL}/django_db_target.py")

        self.assertEqual(result.returncode, 0, _output(result))
        self.assertIn("1 passed", result.stdout, _output(result))

    def test_1_393_prepare_environment_uses_the_root_it_is_given(self):
        """Scenario 1.393 (second half): the plugin hands prepare_environment() the
        root it resolved, and that root's .env is the one loaded."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".env").write_text("LEX_1AW_PROBE=from-this-root\n", encoding="utf-8")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop("LEX_1AW_PROBE", None)
                returned = django_bootstrap.prepare_environment(root)
                self.assertEqual(os.environ.get("LEX_1AW_PROBE"), "from-this-root")
        self.assertEqual(returned, root.resolve())

    # -- 1.394 ---------------------------------------------------------
    def test_1_394_a_project_without_lex_tests_is_left_alone(self):
        """
        Scenario 1.394: inactive elsewhere
        Given: a directory with no lex_test_config.yaml and no payload
        When: pytest runs a test there with the plugin installed
        Then: the plugin does nothing -- no settings module, Django not configured
        """
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp)
            (project / "test_plain.py").write_text(
                "import os\n\n\n"
                "def test_django_is_left_alone():\n"
                "    assert 'DJANGO_SETTINGS_MODULE' not in os.environ\n"
                "    from django.conf import settings\n"
                "    assert not settings.configured\n",
                encoding="utf-8",
            )
            result = _run_pytest("test_plain.py", cwd=project)

        self.assertEqual(result.returncode, 0, _output(result))
        self.assertIn("1 passed", result.stdout, _output(result))

    # -- 1.395 ---------------------------------------------------------
    def test_1_395_p_no_lex_switches_it_off(self):
        """
        Scenario 1.395: the opt-out
        Given: the same test as 1.393
        When: pytest runs it with `-p no:lex`
        Then: the plugin stays out and collection fails the way it did before it existed
        """
        result = _run_pytest("-p", "no:lex", f"{TARGETS_REL}/django_db_target.py")

        self.assertNotEqual(result.returncode, 0, _output(result))
        self.assertIn("settings are not configured", result.stdout + result.stderr, _output(result))

    # -- 1.396 ---------------------------------------------------------
    def test_1_396_pyproject_registers_the_plugin_as_lex(self):
        """
        Scenario 1.396: the entry point
        Given: pyproject.toml
        When: its pytest11 entry points are read
        Then: `lex` names lex.tools.pytest_plugin, which defines the three hooks
        """
        self.assertEqual(_declared_entry_point(), "lex.tools.pytest_plugin")
        module = importlib.import_module(_declared_entry_point())
        for hook in ("pytest_load_initial_conftests", "pytest_configure", "pytest_unconfigure"):
            self.assertTrue(callable(getattr(module, hook, None)), f"{hook} is missing")
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v`
Expected: collection ERROR — `ImportError: cannot import name 'pytest_plugin' from 'lex.tools'`.

- [ ] **Step 4: Let `prepare_environment` take the root**

In `lex/tools/django_bootstrap.py`, replace the signature, docstring head and first line of `prepare_environment`:

```python
def prepare_environment(root: Path | str | None = None) -> Path:
    """Give this process the environment ``lex/bin/lex.py`` gives its own.

    The same steps in the same order: the project's ``.env`` (never overriding
    a variable the process already has), the settings module,
    ``PROJECT_ROOT``, and the package directory on ``sys.path``. Safe to call
    any number of times. Returns the project root -- ``root`` when given (the
    pytest plugin passes the root it resolved), else :func:`project_root`.
    """
    root = Path(root).resolve() if root is not None else project_root()
```

(The rest of the function is unchanged.)

- [ ] **Step 5: Write the plugin**

`lex/tools/pytest_plugin.py`:

```python
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
from dataclasses import dataclass
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

    django.setup()
    early_config.stash[_RUN] = _Run(root=root, test_config=test_config)


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
```

- [ ] **Step 6: Register the entry point**

In `pyproject.toml`, after the `[project.scripts]` table:

```toml
[project.entry-points.pytest11]
# lex's pytest plugin (lex/tools/pytest_plugin.py): gives a plain pytest run --
# PyCharm's runner, VS Code, a terminal -- the setup `lex pytest` does. Active only in
# a project with a lex_test_config.yaml; `-p no:lex` switches it off.
lex = "lex.tools.pytest_plugin"
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v`
Expected: 5 passed (1.393 ×2, 1.394, 1.395, 1.396).

- [ ] **Step 8: Mutation check**

Comment out `django.setup()` in `pytest_load_initial_conftests`; rerun 1.393 → FAIL ("settings are not configured" / apps not ready). Make `is_lex_project` return `True` unconditionally; rerun 1.394 → FAIL. Restore both, rerun → pass.

- [ ] **Step 9: Commit**

```bash
git add lex/tools/pytest_plugin.py lex/tools/django_bootstrap.py pyproject.toml \
  lex/test_project/tests/init/test_1aw_pytest_plugin.py lex/test_project/tests/init/pytest_plugin_targets/
git commit -m "feat(pytest): a plain pytest run is set up the way lex pytest sets one up" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Collect-only, markers and the error paths

**Files:**
- Create: `lex/test_project/tests/init/pytest_plugin_targets/undeclared_marker_target.py`, `.../marked_target.py`
- Test: `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (scenarios 1.397–1.401)

**Interfaces:**
- Consumes: `_run_pytest`, `_output`, `TARGETS_REL` (Task 1); `pytest_plugin._Run`, `pytest_plugin._RUN`, `pytest_plugin.pytest_configure`.

- [ ] **Step 1: Write the two light targets**

`.../pytest_plugin_targets/marked_target.py`:

```python
"""Carries `init`, a group this repository's lex_test_config.yaml declares."""

import pytest

pytestmark = pytest.mark.init


def test_marked():
    assert True
```

`.../pytest_plugin_targets/undeclared_marker_target.py`:

```python
"""Carries a marker lex_test_config.yaml does not declare."""

import pytest

pytestmark = pytest.mark.not_a_configured_group


def test_never_runs():
    assert True
```

- [ ] **Step 2: Write the failing tests (1.397–1.401)**

Append to `test_1aw_pytest_plugin.py`:

```python
# The database the DOCKER-COMPOSE target names when its variables are unset: host
# "envvar_not_existing". Django's start-up never connects in this repository, so
# only an actual database setup fails against it.
_UNREACHABLE_DATABASE = {"DATABASE_DEPLOYMENT_TARGET": "DOCKER-COMPOSE"}
_UNREACHABLE_DROPPED = ("DATABASE_DOMAIN", "DATABASE_NAME", "POSTGRES_PASSWORD")


def _without(*names: str) -> dict:
    return {name: "" for name in names}


class _FakePluginManager:
    """Just the three calls pytest_configure() makes on config.pluginmanager."""

    def __init__(self, active: set[str] = frozenset()):
        self._active = set(active)
        self.registered: list = []

    def hasplugin(self, name: str) -> bool:
        return name in self._active

    def get_plugins(self) -> list:
        return list(self.registered)

    def register(self, plugin, name=None):
        self.registered.append(plugin)


def _fake_config(*, collectonly: bool = False, active: set[str] = frozenset()):
    from types import SimpleNamespace

    from lex.tools.test_groups import resolve_config

    config = SimpleNamespace(
        stash=pytest.Stash(),
        option=SimpleNamespace(collectonly=collectonly),
        pluginmanager=_FakePluginManager(active),
    )
    config.stash[pytest_plugin._RUN] = pytest_plugin._Run(
        root=REPO_ROOT, test_config=resolve_config(REPO_ROOT)
    )
    return config


class TestCluster01aw_MarkersAndFailures(TestCase):
    """Cluster 1aw: collect-only, group markers, and how setup failures are reported."""

    # -- 1.397 ---------------------------------------------------------
    def test_1_397_collect_only_needs_no_database(self):
        """
        Scenario 1.397: listing tests touches no database
        Given: settings pointing at a database host that does not exist
        When: `pytest --collect-only` lists the model-importing target
        Then: it lists the test and exits 0 -- Django and markers set up, no database
        """
        env = {**_UNREACHABLE_DATABASE, **_without(*_UNREACHABLE_DROPPED)}
        result = _run_pytest("--collect-only", "-q", f"{TARGETS_REL}/django_db_target.py", extra_env=env)

        self.assertEqual(result.returncode, 0, _output(result))
        self.assertIn("test_settings_and_test_database", result.stdout, _output(result))

    # -- 1.398 ---------------------------------------------------------
    def test_1_398_a_database_that_cannot_be_set_up_stops_the_run(self):
        """
        Scenario 1.398: a clear failure before any test
        Given: the same unreachable database
        When: the target runs for real
        Then: exit code 4 with "Could not set up the test database", and no test ran
        """
        env = {**_UNREACHABLE_DATABASE, **_without(*_UNREACHABLE_DROPPED)}
        result = _run_pytest(f"{TARGETS_REL}/django_db_target.py", extra_env=env)

        self.assertEqual(result.returncode, 4, _output(result))
        self.assertIn("Could not set up the test database", result.stdout + result.stderr, _output(result))
        self.assertNotIn("passed", result.stdout, _output(result))

    # -- 1.399 ---------------------------------------------------------
    def test_1_399_configured_groups_are_markers(self):
        """
        Scenario 1.399 (first half): no unknown-marker warning
        Given: a module marked `init`, a group lex_test_config.yaml declares
        When: it is collected with unknown-marker warnings turned into errors
        Then: collection succeeds -- the plugin registered the groups as markers
        """
        result = _run_pytest(
            "--collect-only", "-q", "-W", "error::pytest.PytestUnknownMarkWarning",
            f"{TARGETS_REL}/marked_target.py",
        )

        self.assertEqual(result.returncode, 0, _output(result))

    def test_1_399_an_undeclared_marker_aborts_the_run(self):
        """
        Scenario 1.399 (second half): the same rule `lex pytest` enforces
        Given: a module carrying a marker no group declares
        When: a plain pytest run collects it
        Then: exit code 4, naming the marker -- as under `lex pytest`
        """
        result = _run_pytest("--collect-only", "-q", f"{TARGETS_REL}/undeclared_marker_target.py")

        self.assertEqual(result.returncode, 4, _output(result))
        self.assertIn("not_a_configured_group", result.stdout + result.stderr, _output(result))

    # -- 1.400 ---------------------------------------------------------
    def test_1_400_an_invalid_test_config_is_a_usage_error(self):
        """
        Scenario 1.400: invalid lex test config
        Given: a workflow payload without `tests_entrypoint`
        When: a plain pytest run starts
        Then: exit code 4 with the loader's own message
        """
        result = _run_pytest(
            f"{TARGETS_REL}/marked_target.py",
            extra_env={"LEX_TEST_CONFIG_PAYLOAD": '{"groups": []}'},
        )

        self.assertEqual(result.returncode, 4, _output(result))
        self.assertIn("tests_entrypoint", result.stdout + result.stderr, _output(result))

    # -- 1.401 ---------------------------------------------------------
    def test_1_401_pytest_django_alongside_is_refused(self):
        """
        Scenario 1.401: two plugins setting Django's test environment up
        Given: pytest-django active in the same run
        When: the plugin configures the run
        Then: a UsageError says to add `-p no:django`, before anything is set up
        """
        config = _fake_config(active={"django"})

        with self.assertRaises(pytest.UsageError) as raised:
            pytest_plugin.pytest_configure(config)

        self.assertIn("-p no:django", str(raised.exception))
        self.assertEqual(config.pluginmanager.registered, [], "nothing may be registered first")
```

- [ ] **Step 3: Run to see which fail**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v -k "1_397 or 1_398 or 1_399 or 1_400 or 1_401"`
Expected: all pass if Task 1's plugin is complete (these pin behaviour it already has). Then prove each can fail:
- remove the `if config.option.collectonly: return` block → 1.397 FAIL (exit 4, database error);
- replace the `raise pytest.UsageError(...)` in `_set_up_test_run` with `raise` → 1.398 FAIL (exit ≠ 4 / traceback);
- skip the `LexGroupsPlugin` registration → both 1.399 tests FAIL;
- turn the `LexTestConfigError` handler into `pass` with a placeholder config → 1.400 FAIL;
- delete the pytest-django check → 1.401 FAIL.
Restore each change before the next.

- [ ] **Step 4: Run the whole file**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add lex/test_project/tests/init/test_1aw_pytest_plugin.py lex/test_project/tests/init/pytest_plugin_targets/
git commit -m "test(pytest): collect-only, group markers and setup failures under a plain run" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: A project conftest that copied the setup keeps working

**Files:**
- Create: `lex/test_project/tests/init/pytest_plugin_targets/copied_setup_plugin.py`
- Test: `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (scenario 1.402)

**Interfaces:**
- Consumes: `_run_pytest`, `_output`, `TARGETS_REL`, `_fake_config` (Tasks 1–2).

- [ ] **Step 1: Write the stand-in for a copied conftest**

`.../pytest_plugin_targets/copied_setup_plugin.py`:

```python
"""Stands in for a project conftest.py that copied `lex pytest`'s setup.

The known copies guard on _TestState.saved_data and stand down when it exists. Loaded
with `-p` by test_1aw_pytest_plugin.py; a conftest's pytest_configure is an ordinary
hook implementation, as this one is.
"""

_state = {}


def pytest_configure(config):
    from django.test.runner import DiscoverRunner
    from django.test.utils import _TestState, setup_test_environment

    if hasattr(_TestState, "saved_data"):
        print("COPIED-SETUP: stood down")
        return
    setup_test_environment()
    runner = DiscoverRunner(verbosity=0, interactive=False, keepdb=True)
    _state["runner"], _state["old"] = runner, runner.setup_databases(aliases={"default"})
    print("COPIED-SETUP: set up")


def pytest_unconfigure(config):
    from django.test.utils import teardown_test_environment

    if "runner" in _state:
        _state["runner"].teardown_databases(_state["old"])
        teardown_test_environment()
```

- [ ] **Step 2: Write the tests (1.402)**

Append:

```python
class TestCluster01aw_CopiedSetupCoexists(TestCase):
    """Cluster 1aw: projects that copied the setup into a conftest are not broken."""

    # -- 1.402 ---------------------------------------------------------
    def test_1_402_a_copied_setup_finds_the_work_done(self):
        """
        Scenario 1.402 (first half): the plugin goes first
        Given: a project conftest that copied `lex pytest`'s setup and guards on it
        When: a plain pytest run loads both
        Then: the plugin's pytest_configure ran first, the copy stood down, and the
              test passes -- no "setup_test_environment() was already called"
        """
        result = _run_pytest(
            "-s", "-p", "lex.test_project.tests.init.pytest_plugin_targets.copied_setup_plugin",
            f"{TARGETS_REL}/django_db_target.py",
        )

        self.assertEqual(result.returncode, 0, _output(result))
        self.assertIn("COPIED-SETUP: stood down", result.stdout, _output(result))

    def test_1_402_the_plugin_stands_down_when_set_up_already(self):
        """
        Scenario 1.402 (second half): something else went first
        Given: a test environment already set up (this suite's own, by `lex pytest`)
        When: the plugin configures and unconfigures a run
        Then: it sets nothing up and tears nothing down
        """
        from django.test.utils import _TestState

        self.assertTrue(hasattr(_TestState, "saved_data"), "precondition: run under `lex pytest`")
        config = _fake_config()

        with patch("django.test.utils.setup_test_environment") as setup, \
                patch("django.test.runner.DiscoverRunner.setup_databases") as setup_databases, \
                patch("django.test.utils.teardown_test_environment") as teardown:
            pytest_plugin.pytest_configure(config)
            pytest_plugin.pytest_unconfigure(config)

        setup.assert_not_called()
        setup_databases.assert_not_called()
        teardown.assert_not_called()
        self.assertFalse(config.stash[pytest_plugin._RUN].test_environment)
```

- [ ] **Step 3: Run, then prove each half can fail**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v -k 1_402` → 2 passed.
Mutations: remove `tryfirst=True` from the plugin's `pytest_configure` → first half FAIL (the copy sets up first; the plugin then calls `setup_test_environment()` again → RuntimeError, or the copy prints "set up"). Delete the `_TestState.saved_data` guard in the plugin → second half FAIL (`setup` called). Restore.

- [ ] **Step 4: Commit**

```bash
git add lex/test_project/tests/init/test_1aw_pytest_plugin.py lex/test_project/tests/init/pytest_plugin_targets/copied_setup_plugin.py
git commit -m "test(pytest): a project conftest that copied lex pytest's setup still works" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `lex pytest` and `lex pytest-groups` run on the plugin

**Files:**
- Modify: `lex/bin/lex.py:998-1089` (`pytest_cmd`: from the `_bootstrap_django()` call through the `finally` teardown)
- Modify: `lex/tools/test_groups.py:785-792` (`collect_groups`)
- Modify: `lex/lex_app/management/commands/tests/test_lex_pytest_report_flags.py:113`
- Test: `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (scenario 1.403)

**Interfaces:**
- Consumes: `lex.tools.pytest_plugin` (the module object).
- Produces: `pytest.main(["-p", "no:lex", *forwarded], plugins=[pytest_plugin, groups_plugin])` from `pytest_cmd`; `pytest.main(["-p", "no:lex", "--collect-only", "-q", entrypoint, *extra], plugins=[pytest_plugin, plugin, recorder])` from `collect_groups`.

- [ ] **Step 1: Write the failing tests (1.403)**

Append:

```python
class TestCluster01aw_LexPytestSharesThePlugin(TestCase):
    """Cluster 1aw: `lex pytest` and `lex pytest-groups` hand setup to the plugin."""

    def _config(self, root: Path):
        from lex.tools.test_groups import GroupConfig, LexTestConfig

        (root / "Tests").mkdir()
        return LexTestConfig(
            project_root=root, source="test", tests_entrypoint="Tests",
            report_output_dir="reports", global_receivers=[],
            email={"from_email": "", "from_name": "", "reply_to": "", "subject_prefix": ""},
            groups=[GroupConfig(name="init", description="", receivers=[])],
            group_assignments={},
        )

    # -- 1.403 ---------------------------------------------------------
    def test_1_403_lex_pytest_passes_the_plugin_and_sets_nothing_up_itself(self):
        """
        Scenario 1.403: one implementation
        Given: `lex pytest` with no target
        When: it calls pytest
        Then: it passes the plugin module with `-p no:lex` ahead of the entrypoint,
              and calls setup_test_environment() itself no more
        """
        from click.testing import CliRunner

        from lex.bin.lex import lex

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(Path(tmp))
            with patch("lex.bin.lex.os.chdir"), \
                    patch("lex.bin.lex._has_explicit_pytest_target", return_value=False), \
                    patch("lex.tools.test_groups.resolve_config", return_value=config), \
                    patch("django.test.utils.setup_test_environment") as setup, \
                    patch("pytest.main", return_value=0) as pytest_main:
                result = CliRunner().invoke(lex, ["pytest", "-q"])

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = pytest_main.call_args
        self.assertEqual(args[0], ["-p", "no:lex", "Tests", "-q"])
        self.assertIs(kwargs["plugins"][0], pytest_plugin)
        setup.assert_not_called()

    def test_1_403_lex_pytest_groups_passes_the_plugin(self):
        """Scenario 1.403 (second half): the collect-only listing gets Django and the
        markers from the same plugin."""
        from click.testing import CliRunner

        from lex.bin.lex import lex

        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(Path(tmp))
            with patch("lex.bin.lex.os.chdir"), patch("lex.bin.lex._bootstrap_django"), \
                    patch("lex.tools.test_groups.resolve_config", return_value=config), \
                    patch("pytest.main", return_value=0) as pytest_main:
                result = CliRunner().invoke(lex, ["pytest-groups"])

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = pytest_main.call_args
        self.assertEqual(args[0][:3], ["-p", "no:lex", "--collect-only"])
        self.assertIs(kwargs["plugins"][0], pytest_plugin)
```

- [ ] **Step 2: Run to verify they fail**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v -k 1_403`
Expected: FAIL — `args[0]` is `["Tests", "-q"]`; `setup_test_environment` was called (or RuntimeError, since this suite already set it up).

- [ ] **Step 3: Rewire `pytest_cmd`**

In `lex/bin/lex.py`, replace everything from the comment `# Bootstrap Django after starting report coverage so app/model import-time` (before `_bootstrap_django()`) down to and including the line `exit_code = _pytest.main(forwarded, plugins=[plugin])`, and the `finally:` block's patch/database/test-environment teardown, so that the section reads:

```python
    # The plugin sets Django, the test environment and the test database up, and
    # undoes them afterwards -- the code a plain pytest run (PyCharm, VS Code) gets
    # through the `pytest11` entry point. It is passed in directly so it runs even
    # where that entry point is not installed; `-p no:lex` stops pytest loading the
    # same module a second time through the entry point. django.setup() runs inside
    # pytest.main(), after coverage started, so import-time code is still measured.
    from lex.tools import pytest_plugin

    started_at = time.perf_counter()
    try:
        exit_code = _pytest.main(["-p", "no:lex", *forwarded], plugins=[pytest_plugin, plugin])
    except LexTestConfigError as exc:
        # Raised from pytest_collection_modifyitems on marker/group mismatch.
        raise click.ClickException(str(exc)) from exc
    finally:
        plugin.run_duration = f"{time.perf_counter() - started_at:.1f} s"
        if coverage_runner is not None:
```

Keep the coverage block inside `finally` exactly as it is.

- [ ] **Step 4: Rewire `collect_groups`**

In `lex/tools/test_groups.py::collect_groups`, replace the argument list and the call:

```python
    from lex.tools import pytest_plugin

    # The same setup a `lex pytest` run gets; collect-only, so no database.
    args = ["-p", "no:lex", "--collect-only", "-q", config.tests_entrypoint]
    if extra_args:
        args.extend(extra_args)

    previous_cwd = Path.cwd()
    try:
        os.chdir(config.project_root)
        exit_code = _pytest.main(args, plugins=[pytest_plugin, plugin, recorder])
    finally:
        os.chdir(previous_cwd)
```

- [ ] **Step 5: Update the report-flag test to the new call**

In `lex/lex_app/management/commands/tests/test_lex_pytest_report_flags.py`, add `from lex.tools import pytest_plugin` to the imports and change line 113 to:

```python
        pytest_main.assert_called_once_with(["-p", "no:lex", "Tests", "-q"], plugins=[pytest_plugin, plugin])
```

- [ ] **Step 6: Run**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py lex/lex_app/management/commands/tests/test_lex_pytest_report_flags.py -v`
Expected: 1aw all pass; the four report-flag tests pass (two of them failed before this change, because `pytest_cmd` set the test environment up a second time inside a running suite).

- [ ] **Step 7: The suite still runs through `lex pytest`**

Run: `$LEXPYTEST lex/test_project/tests/init lex/test_project/tests/crud_api -q`
Expected: the same pass/fail counts as on `origin/lex-app-v2` for those two folders (record both numbers).

- [ ] **Step 8: Commit**

```bash
git add lex/bin/lex.py lex/tools/test_groups.py lex/lex_app/management/commands/tests/test_lex_pytest_report_flags.py \
  lex/test_project/tests/init/test_1aw_pytest_plugin.py
git commit -m "refactor(pytest): lex pytest and pytest-groups run on the plugin" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Test run configurations for PyCharm and VS Code

**Files:**
- Modify: `generate_pycharm_configs.py` (constants near the top; `generate_pycharm_configs`; `generate_vscode_configs`)
- Test: `lex/test_project/tests/init/test_1aw_pytest_plugin.py` (scenario 1.404)

**Interfaces:**
- Produces: `TEST_RUN_CONFIG_ENVS`, `_test_run_configs(root) -> list[dict]` (keys `name`, `pycharm_filename`, `target`, `args`), `_pycharm_test_config_xml(config, root, project_name, env_files_option) -> str`, `_build_vscode_test_config(config, env_file_exists) -> dict`.

- [ ] **Step 1: Write the failing tests (1.404)**

Append:

```python
class TestCluster01aw_TestRunConfigurations(TestCase):
    """Cluster 1aw: `lex setup` writes the suite and each group as test configurations."""

    def _project(self, root: Path, *, with_config: bool) -> None:
        (root / "Tests").mkdir()
        (root / ".env").write_text("", encoding="utf-8")
        if with_config:
            (root / "lex_test_config.yaml").write_text(
                "tests_entrypoint: Tests\n"
                "groups:\n"
                "  - {name: alpha, description: first}\n"
                "  - {name: beta, description: second}\n",
                encoding="utf-8",
            )

    def _pycharm(self, path: Path) -> dict:
        import json
        from xml.etree import ElementTree

        configuration = ElementTree.parse(path).getroot().find(".//configuration")
        options = {o.attrib["name"]: o.attrib.get("value", "") for o in configuration.findall("option")}
        return {
            "attrs": dict(configuration.attrib),
            "options": options,
            "target": json.loads(options["_new_target"]),
            "target_type": json.loads(options["_new_targetType"]),
            "arguments": json.loads(options["_new_additionalArguments"]),
            "env": {e.attrib["name"]: e.attrib["value"] for e in configuration.findall("./envs/env")},
        }

    # -- 1.404 ---------------------------------------------------------
    def test_1_404_the_suite_and_each_group_become_test_configurations(self):
        """
        Scenario 1.404: one click to PyCharm's test tree
        Given: a project whose lex_test_config.yaml declares groups alpha and beta
        When: the run configurations are generated
        Then: PyCharm gets Tests, Tests: alpha and Tests: beta as pytest
              configurations on tests_entrypoint (-m <group> for a group), and VS
              Code gets the same three as pytest launch entries
        """
        import json

        from generate_pycharm_configs import generate_pycharm_configs, generate_vscode_configs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._project(root, with_config=True)
            generate_pycharm_configs(root)
            generate_vscode_configs(root)

            suite = self._pycharm(root / ".run" / "Tests.run.xml")
            alpha = self._pycharm(root / ".run" / "Tests_alpha.run.xml")
            self.assertTrue((root / ".run" / "Tests_beta.run.xml").exists())
            launch = json.loads((root / ".vscode" / "launch.json").read_text(encoding="utf-8"))

        self.assertEqual(suite["attrs"]["type"], "tests")
        self.assertEqual(suite["attrs"]["factoryName"], "py.test")
        self.assertEqual(suite["attrs"]["name"], "Tests")
        self.assertEqual(suite["target_type"], "PATH")
        self.assertEqual(suite["target"], (root / "Tests").as_posix())
        self.assertEqual(suite["arguments"], "")
        self.assertEqual(suite["options"]["WORKING_DIRECTORY"], str(root))
        self.assertEqual(suite["env"], {"PYTHONUNBUFFERED": "1", "CELERY_ACTIVE": "False"})
        self.assertEqual(alpha["attrs"]["name"], "Tests: alpha")
        self.assertEqual(alpha["arguments"], "-m alpha")

        by_name = {c["name"]: c for c in launch["configurations"]}
        self.assertEqual(by_name["LEX: Tests"]["module"], "pytest")
        self.assertEqual(by_name["LEX: Tests"]["args"], ["Tests"])
        self.assertEqual(by_name["LEX: Tests: beta"]["args"], ["-m", "beta", "Tests"])
        self.assertEqual(by_name["LEX: Tests"]["env"], {"PYTHONUNBUFFERED": "1", "CELERY_ACTIVE": "False"})

    def test_1_404_no_test_configurations_without_a_test_config(self):
        """Scenario 1.404 (second half): a project without lex_test_config.yaml gets
        the usual configurations and no test ones."""
        import json

        from generate_pycharm_configs import generate_pycharm_configs, generate_vscode_configs

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            self._project(root, with_config=False)
            generate_pycharm_configs(root)
            generate_vscode_configs(root)
            run_files = sorted(p.name for p in (root / ".run").glob("Tests*.run.xml"))
            launch = json.loads((root / ".vscode" / "launch.json").read_text(encoding="utf-8"))

        self.assertEqual(run_files, [])
        self.assertFalse(any(c["name"].startswith("LEX: Tests") for c in launch["configurations"]))
```

- [ ] **Step 2: Run to verify they fail**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py -v -k 1_404`
Expected: first FAIL (`Tests.run.xml` missing); second passes already.

- [ ] **Step 3: Implement**

In `generate_pycharm_configs.py`, after `VSCODE_IDE = "vscode"`:

```python
# Test configurations: CELERY_ACTIVE=False so a local run calculates in-process
# instead of handing work to a Celery worker that may not be running. The pytest
# plugin itself leaves Celery alone, so `lex pytest` elsewhere is unaffected.
TEST_RUN_CONFIG_ENVS = {"PYTHONUNBUFFERED": "1", "CELERY_ACTIVE": "False"}
TEST_RUN_CONFIG_NAME = "Tests"
```

After `_vscode_args`:

```python
def _test_run_configs(root: Path) -> list[dict]:
    """The suite and each test group, from the project's lex_test_config.yaml.

    None when the file is missing or invalid: a project without lex tests gets no
    test configurations.
    """
    from lex.tools.test_groups import LexTestConfigError, load_config

    try:
        test_config = load_config(root)
    except LexTestConfigError:
        return []

    suite = {
        "name": TEST_RUN_CONFIG_NAME,
        "pycharm_filename": f"{TEST_RUN_CONFIG_NAME}.run.xml",
        "target": test_config.tests_entrypoint,
        "args": (),
    }
    groups = [
        {
            "name": f"{TEST_RUN_CONFIG_NAME}: {group.name}",
            "pycharm_filename": f"{TEST_RUN_CONFIG_NAME}_{group.name}.run.xml",
            "target": test_config.tests_entrypoint,
            "args": ("-m", group.name),
        }
        for group in test_config.groups
    ]
    return [suite, *groups]


def _xml_json(value: str) -> str:
    """PyCharm stores a test configuration's target and arguments as JSON strings."""
    return escape(json.dumps(value), {'"': "&quot;"})


def _pycharm_test_config_xml(config, root: Path, project_name: str, env_files_option: str) -> str:
    """A pytest configuration as PyCharm saves one: type ``tests``, factory ``py.test``,
    fields ``_new_*`` -- read from PyCharm's ``PyTestFactory`` and ``PyTestsShared``."""
    target = (root / config["target"]).as_posix()
    return f"""<component name="ProjectRunConfigurationManager">
  <configuration default="false" name="{escape(config['name'])}" type="tests" factoryName="py.test">
    <module name="{escape(project_name)}" />
    {env_files_option}
    <option name="INTERPRETER_OPTIONS" value="" />
    <option name="PARENT_ENVS" value="true" />
    <envs>
{_render_envs(TEST_RUN_CONFIG_ENVS)}
    </envs>
    <option name="SDK_HOME" value="" />
    <option name="WORKING_DIRECTORY" value="{escape(str(root))}" />
    <option name="IS_MODULE_SDK" value="true" />
    <option name="ADD_CONTENT_ROOTS" value="true" />
    <option name="ADD_SOURCE_ROOTS" value="true" />
    <EXTENSION ID="PythonCoverageRunConfigurationExtension" runner="coverage.py" />
    <option name="_new_keywords" value="{_xml_json('')}" />
    <option name="_new_parameters" value="{_xml_json('')}" />
    <option name="_new_additionalArguments" value="{_xml_json(' '.join(config['args']))}" />
    <option name="_new_target" value="{_xml_json(target)}" />
    <option name="_new_targetType" value="{_xml_json('PATH')}" />
    <method v="2" />
  </configuration>
</component>"""
```

In `generate_pycharm_configs`, after the `for config in RUN_CONFIGS:` loop and before the success print:

```python
    for config in _test_run_configs(root):
        path = runconfigs_dir / config["pycharm_filename"]
        path.write_text(
            _pycharm_test_config_xml(config, root, project_name, env_files_option),
            encoding="utf-8",
        )
        print(f"[OK] Generated: {config['pycharm_filename']}")
```

After `_build_vscode_config`:

```python
def _build_vscode_test_config(config, env_file_exists: bool) -> dict:
    launch_config = {
        "name": f"{VSCODE_CONFIG_PREFIX}{config['name']}",
        "type": "debugpy",
        "request": "launch",
        "module": "pytest",
        "args": [*config["args"], config["target"]],
        "cwd": "${workspaceFolder}",
        "console": "integratedTerminal",
        "env": dict(TEST_RUN_CONFIG_ENVS),
    }
    if env_file_exists:
        launch_config["envFile"] = "${workspaceFolder}/.env"
    return launch_config
```

In `generate_vscode_configs`, compute `test_configs = _test_run_configs(root)` right after `launch_config = _read_existing_launch_config(launch_path)`, add their names to `generated_config_names`:

```python
    test_configs = _test_run_configs(root)
    generated_config_names = {
        f"{VSCODE_CONFIG_PREFIX}{config['name']}" for config in [*RUN_CONFIGS, *test_configs]
    }
```

and append them to `generated_configs`:

```python
    env_file_exists = (root / ".env").exists()
    generated_configs = [
        _build_vscode_config(config, env_file_exists) for config in RUN_CONFIGS
    ] + [_build_vscode_test_config(config, env_file_exists) for config in test_configs]
```

and print them with the others:

```python
    for config in [*RUN_CONFIGS, *test_configs]:
        print(f"[OK] Generated: {VSCODE_CONFIG_PREFIX}{config['name']}")
```

- [ ] **Step 4: Run, including the existing run-config cluster**

Run: `$LEXPYTEST lex/test_project/tests/init/test_1aw_pytest_plugin.py lex/test_project/tests/init/test_1y_ide_run_configs.py -v`
Expected: all pass (1.207's parity scaffold has no lex_test_config.yaml, so it sees no test configurations).

- [ ] **Step 5: Commit**

```bash
git add generate_pycharm_configs.py lex/test_project/tests/init/test_1aw_pytest_plugin.py
git commit -m "feat(setup): the suite and each test group as PyCharm and VS Code test configurations" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Keep the CI-helper tests plain

**Files:**
- Create: `.github/scripts/pytest.ini`
- Modify: `.github/workflows/scripts_tests.yml` (comments on the `pyproject.toml` path entries and the `Run tests` step)

- [ ] **Step 1: Write the ini**

`.github/scripts/pytest.ini`:

```ini
[pytest]
# The helpers under .github/scripts/ are CI scripts, not framework code: plain
# pytest, no Django, no database. Two plugins would otherwise switch themselves on
# wherever lex-app is installed with them:
#   lex     -- lex's own plugin, active in any project with a lex_test_config.yaml;
#              this repository has one, so it would set Django and the shared test
#              database up for every run of these tests.
#   django  -- pytest-django, where installed.
# Being the nearest ini, this file also makes .github/scripts the rootdir for them.
addopts = -p no:lex -p no:django
```

- [ ] **Step 2: Update the workflow comments**

In `.github/workflows/scripts_tests.yml`, in both `paths:` lists replace

```yaml
      - "pyproject.toml"          # supplies pytest's addopts, and decides what
                                  # the wheel ships
```

with

```yaml
      - "pyproject.toml"          # decides what the wheel ships
```

and replace the `Run tests` step's comment with:

```yaml
        # -p no:cacheprovider keeps the runner from writing .pytest_cache.
        # .github/scripts/pytest.ini is the nearest ini, so its addopts apply:
        # `-p no:lex -p no:django`. Neither plugin is installed on this runner,
        # and disabling a missing plugin is a no-op rather than an error.
```

- [ ] **Step 3: Run the helper tests the way CI does, and the way a developer does**

Run: `python3 -m pytest .github/scripts/tests/ -q -p no:cacheprovider` (system Python with pytest + pyyaml, lex-app not installed) and `/home/syscall/Documents/lex/.venv-test/bin/python -m pytest .github/scripts/tests/ -q -p no:cacheprovider`
Expected: both pass with the same count as before the change (record it); the second does not touch the database (completes in seconds, no "Creating test database" / migration output).

- [ ] **Step 4: Commit**

```bash
git add .github/scripts/pytest.ini .github/workflows/scripts_tests.yml
git commit -m "ci(scripts): the CI-helper tests stay plain pytest with lex's plugin installed" \
  -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Test-plan sync

**Files:**
- Modify: `lex/test_project/test-plan/clusters/01-init/allocation.yaml` (`max_scenario: 385` → `404`; letter `aw`)
- Modify: `lex/test_project/test-plan/clusters/01-init/batches.md` (append `### Batch 1aw`)
- Modify: `lex/test_project/test-plan/clusters/01-init/cluster.md` (append `### 1aw.` section)
- Create: `lex/test_project/test-plan/progress/sessions/2026-10-15-1aw-pytest-plugin.md`
- Regenerate: `lex/test_project/test-plan/progress/dashboard.md`

- [ ] **Step 1: Allocation**

Set `max_scenario: 404` and append:

```yaml
  aw:
    title: A plain pytest run -- PyCharm, VS Code, a terminal -- is set up like `lex pytest`
    scenarios: 1.393-1.404
    status: complete
    tests:
      pass: <N from Task 8>
      skip: 0
      xfail: 0
    note: >-
      PyCharm's runner calls pytest.main() directly, so none of `lex pytest`'s setup ran
      and its test tree showed one collection error ("settings are not configured")
      instead of the tests; projects copied the setup into a root conftest.py.
      lex/tools/pytest_plugin.py, the pytest11 entry point `lex`, now does it in any
      project with a lex_test_config.yaml, and `lex pytest` / `lex pytest-groups` pass
      the same module with `-p no:lex`. Child-process scenarios run `python -m pytest`
      without the suite's environment, with the entry point registered through a
      temporary dist-info so they take pytest's autoload path whatever is installed.
      1av (1.386-1.392) is reserved by an open PR, hence aw.
```

- [ ] **Step 2: Batch block**

Append to `batches.md`, mirroring `### Batch 1au`:

```markdown
### Batch 1aw — a plain pytest run is set up like `lex pytest`: PyCharm's test tree works ✅

| Property | Value |
| --- | --- |
| Scenario range | 1.393 – 1.404 |
| Type | U / I |
| Files covered | `lex/tools/pytest_plugin.py` (new); `lex/tools/django_bootstrap.py` (`prepare_environment(root)`); `lex/bin/lex.py` (`pytest_cmd`); `lex/tools/test_groups.py` (`collect_groups`); `generate_pycharm_configs.py` (test run configurations); `pyproject.toml` (the `pytest11` entry point) |
| Test file | `lex/test_project/tests/init/test_1aw_pytest_plugin.py` |
| Test classes | `TestCluster01aw_PlainRunIsSetUp` (1.393–1.396), `TestCluster01aw_MarkersAndFailures` (1.397–1.401), `TestCluster01aw_CopiedSetupCoexists` (1.402), `TestCluster01aw_LexPytestSharesThePlugin` (1.403), `TestCluster01aw_TestRunConfigurations` (1.404) |
| Fixtures | `pytest_plugin_targets/` — modules a child `python -m pytest` runs by explicit path; a temporary dist-info that registers the entry point; `DATABASE_DEPLOYMENT_TARGET=DOCKER-COMPOSE` with its variables unset, for a database that cannot be reached |
| Tests landed | **<N> pass / 0 fail** |
| Status | ✅ Complete |

| Scenario | Title | Asserts |
| --- | --- | --- |
| 1.393 | the PyCharm way | `python -m pytest` on a model-importing, database-writing test passes with none of `lex pytest`'s environment; `prepare_environment(root)` loads that root's `.env` |
| 1.394 | inactive elsewhere | in a directory without `lex_test_config.yaml`, a test sees no settings module and unconfigured Django |
| 1.395 | the opt-out | `-p no:lex` brings back "settings are not configured" |
| 1.396 | the entry point | pyproject registers `lex = "lex.tools.pytest_plugin"`, which defines the three hooks |
| 1.397 | collect-only needs no database | against an unreachable database `--collect-only` lists the test and exits 0 |
| 1.398 | a database that cannot be set up | the same run for real exits 4 with "Could not set up the test database" and runs no test |
| 1.399 | groups are markers | a configured group raises no unknown-marker warning; an undeclared marker exits 4 |
| 1.400 | invalid test config | a payload without `tests_entrypoint` exits 4 with the loader's message |
| 1.401 | pytest-django alongside | the plugin refuses with a message naming `-p no:django`, registering nothing |
| 1.402 | a copied conftest | it stands down when the plugin went first; the plugin stands down when the environment was set up already |
| 1.403 | one implementation | `lex pytest` passes the module with `-p no:lex` and sets nothing up itself; `lex pytest-groups` passes it too |
| 1.404 | test run configurations | `Tests`, `Tests: <group>` as PyCharm pytest configurations and VS Code pytest launch entries; none without a test config |

**Mutations: <record each from Tasks 1–5>.**
```

- [ ] **Step 3: Cluster section**

Append to `cluster.md`, mirroring `### 1au.`: a `### 1aw. A plain pytest run is set up like \`lex pytest\` — PyCharm's test tree works ✅` heading, a two-sentence summary, the same 12-row scenario table (ID / Title / Then), and the trailer line `**Scenario range:** 1.393 – 1.404. **Test file:** \`lex/test_project/tests/init/test_1aw_pytest_plugin.py\`. **Type:** U / I. **Status:** ✅ Complete (2026-10-15). Batch details: [batches.md](batches.md).`

- [ ] **Step 4: Session fragment**

`lex/test_project/test-plan/progress/sessions/2026-10-15-1aw-pytest-plugin.md`:

```markdown
---
date: 2026-10-15
clusters: [1]
tests_added: "12 scenarios (1.393-1.404), <N> tests"
suite_tally: "<from Task 8>"
---

# Batch 1aw — a plain pytest run is set up like `lex pytest`

PyCharm's test tree showed one collection error for every lex project, because its
runner calls pytest.main() directly and none of `lex pytest`'s setup ran. lex-app now
ships that setup as a pytest plugin; `lex pytest` uses the same module. Details:
[batch 1aw](../../clusters/01-init/batches.md).
```

- [ ] **Step 5: Regenerate the dashboard and validate**

Run: `/home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py build && /home/syscall/Documents/lex/.venv-test/bin/python .github/scripts/test_plan_aggregates.py check`
Expected: `build` rewrites `progress/dashboard.md`; `check` exits 0. Then `... validate` reports no new problems beyond the baseline count (record it before and after).

- [ ] **Step 6: Commit**

```bash
git add lex/test_project/test-plan
git commit -m "docs(test-plan): batch 1aw, the pytest plugin" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Verification, then the pull request

- [ ] **Step 1: PyCharm's own runner**

```bash
H=/home/syscall/.local/share/JetBrains/Toolbox/apps/pycharm/plugins/python-ce/helpers/pycharm
EP=$(mktemp -d); mkdir -p "$EP/lex_pytest_entry_point-0.dist-info"
printf 'Metadata-Version: 2.1\nName: lex-pytest-entry-point\nVersion: 0\n' > "$EP/lex_pytest_entry_point-0.dist-info/METADATA"
printf '[pytest11]\nlex = lex.tools.pytest_plugin\n' > "$EP/lex_pytest_entry_point-0.dist-info/entry_points.txt"
env -u DJANGO_SETTINGS_MODULE -u PROJECT_ROOT PYTHONPATH="$H:$EP:$PWD" \
  /home/syscall/Documents/lex/.venv-test/bin/python $H/_jb_pytest_runner.py --path lex/test_project/tests/crud_api/test_2a_create.py
```

Expected: `##teamcity[testStarted` / `testFinished` per test, no `top_level_collect`, no `testFailed` (before: one failed `top_level_collect`, `testCount count='0'`).

- [ ] **Step 2: Full cluster suite, A/B against the base**

Run the full suite on this branch with `$LEXPYTEST` (nothing else running), compare with the known baseline categories (24 cluster-15 rows when run after cluster 6, 6 Reflex CLI, 5 stress timing, the gate self-test). Expected: no new failure category; 1aw all pass. Fill `<N>` and the tallies into Task 7's files and amend that commit.

- [ ] **Step 3: Push and open the PR**

```bash
git push git@github.com:ExcellenceCloudGmbH/lex-app.git feat/pytest-plugin-ide-runner
gh pr create --repo ExcellenceCloudGmbH/lex-app --base lex-app-v2 --head feat/pytest-plugin-ide-runner \
  --title "feat(pytest): run a lex project's tests in PyCharm's own test runner" --body-file <body.md>
```

Body: what was broken (PyCharm's test tree), what ships (the plugin, `lex pytest` on it, test configurations), how to opt out, compatibility (editable installs reinstall; copied conftests keep working and can be deleted), verification (counts, PyCharm runner). No customer names, no issue IDs; ends with the Claude Code line.

---

### Task 9: Docs (lex-app-docs)

**Files (in a lex-app-docs worktree off `main`):**
- Modify: `content/reference/CLI Commands.md` — the Testing Commands section.

- [ ] **Step 1: Write the section** — after the paragraph that starts "`lex pytest` behaves like plain `pytest`": a `### Running tests in PyCharm or VS Code` subsection: the same setup applies to a plain pytest run in any project with a `lex_test_config.yaml`, so PyCharm's test tree, gutter buttons and "Run pytest in …" work with nothing to add; `lex setup` (or `lex-generate-configs`) writes a **Tests** configuration and one per group; `-p no:lex` switches the plugin off; `lex pytest` stays the command for `--report` and CI; a project that copied this setup into its own `conftest.py` can delete it after upgrading.
- [ ] **Step 2: Replace the `lex_test_config.yaml` key table** with the real keys: `tests_entrypoint` (directory pytest collects from, relative to the project root; required), `groups` (non-empty list of `name` + `description`; every name is a marker), `receivers` (report recipients), `email.from_email` / `from_name` / `reply_to` / `subject_prefix`, `report.output_dir` (default `reports`). Check each against `lex/tools/test_groups.py::_load_config_mapping` before writing it.
- [ ] **Step 3: One commit, one push** (lex-app-docs rejects a second push to an open PR's branch), PR against `main` linking the lex-app PR.
