"""A Lex project inside a larger repository runs without extra environment variables.

Intent
------
The CLI took the git toplevel as the project root. A Lex project that shares a
repository with other code (a monorepo with a web client beside it) was then
the wrong folder: lex_config.py was not found, the models were discovered from
the repository root, and the project's own settings module was never used. The
project needed a wrapper that set GIT_CEILING_DIRECTORIES, PYTHONPATH and
DJANGO_SETTINGS_MODULE. Now the nearest folder with a lex_config.py is the
project root, and a settings.py in that folder is the settings module
(``<folder>.settings``), importable without PYTHONPATH. Projects without either
file behave as before.

Cluster 1ay — scenarios 1.402–1.408. Type: U.
Covers: lex/tools/project_root.py (find_project_root, project_settings_module,
apply_settings_module), lex/tools/django_bootstrap.py (prepare_environment),
lex/bin/lex.py (module start).
Run: python -m lex pytest lex/test_project/tests/init/test_1ay_nested_project.py -v
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
from django.test import SimpleTestCase

from lex.tools import project_root as project_root_module
from lex.tools.project_root import find_project_root, project_settings_module

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(project_root_module.__file__).resolve().parents[2]

#: Variables the fresh interpreters must not inherit from the test process.
_CLEARED = ("DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "GIT_CEILING_DIRECTORIES")


def _monorepo() -> tuple[Path, Path]:
    """A git repository with a Lex project in ``backend/`` and a web client beside it."""
    repo = Path(tempfile.mkdtemp(prefix="cluster1ay_")).resolve()
    subprocess.run(["git", "init", "-q", repo.as_posix()], check=True)
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text("{}")
    project = repo / "backend"
    (project / "Inputs").mkdir(parents=True)
    (project / "__init__.py").write_text("")
    (project / "lex_config.py").write_text("API_PREFIX = 'api/backend/'\n")
    return repo, project


def _fresh(code: str, cwd: Path) -> dict:
    """Run ``code`` in a fresh interpreter in ``cwd``; it prints one JSON object."""
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=120
    )
    assert result.returncode == 0, f"fresh interpreter failed:\n{result.stderr}"
    return json.loads(result.stdout.strip().splitlines()[-1])


_PROBE = """
import importlib.util, json, os
{start}
module = os.environ["DJANGO_SETTINGS_MODULE"]
print(json.dumps({{
    "module": module,
    "importable": importlib.util.find_spec(module) is not None,
    "project_root": os.environ.get("PROJECT_ROOT"),
}}))
"""


class TestCluster01ay_NestedProject(SimpleTestCase):
    """Cluster 1ay: a Lex project nested in a repository."""

    # -- 1.402 ---------------------------------------------------------
    def test_1_402_the_folder_with_lex_config_is_the_root_below_git(self) -> None:
        """
        Scenario 1.402: lex_config.py marks the project root inside a git repository.
        Given: a git repository with a Lex project in backend/
        When: the root is resolved from backend/ and from a folder inside it
        Then: both give backend/, not the git toplevel
        """
        repo, project = _monorepo()
        self.assertEqual(
            Path(find_project_root(project)), project, "the project folder must win over the git toplevel"
        )
        self.assertEqual(
            Path(find_project_root(project / "Inputs")),
            project,
            "a folder inside the project must resolve to the project, not the repository",
        )

    # -- 1.403 ---------------------------------------------------------
    def test_1_403_without_lex_config_the_git_toplevel_stays_the_root(self) -> None:
        """
        Scenario 1.403: projects without lex_config.py keep the git toplevel.
        Given: a git repository whose subfolder has no lex_config.py
        When: the root is resolved from the subfolder
        Then: the git toplevel, as before
        """
        repo, project = _monorepo()
        (project / "lex_config.py").unlink()
        self.assertEqual(
            Path(find_project_root(project)), repo, "without lex_config.py the git toplevel must stay the root"
        )

    # -- 1.404 ---------------------------------------------------------
    def test_1_404_a_project_settings_py_is_the_settings_module(self) -> None:
        """
        Scenario 1.404: the settings module follows the project's settings.py.
        Given: a project folder with and without settings.py, and one named not as an identifier
        When: the settings module is chosen
        Then: <folder>.settings with the file, lex_app.settings without it or for an unimportable name
        """
        repo, project = _monorepo()
        self.assertEqual(project_settings_module(project), "lex_app.settings", "no settings.py: Lex's own")
        (project / "settings.py").write_text("from lex_app.settings import *\n")
        self.assertEqual(project_settings_module(project), "backend.settings", "settings.py: the project's")
        dashed = repo / "my-backend"
        dashed.mkdir()
        (dashed / "settings.py").write_text("")
        self.assertEqual(
            project_settings_module(dashed), "lex_app.settings", "a folder name that is no identifier cannot be imported"
        )

    # -- 1.405 ---------------------------------------------------------
    def test_1_405_the_cli_starts_a_nested_project_with_its_settings(self) -> None:
        """
        Scenario 1.405: python -m lex in a nested project needs no variables.
        Given: the monorepo project with a settings.py, and no DJANGO_SETTINGS_MODULE, PYTHONPATH or PROJECT_ROOT
        When: the CLI module starts in backend/
        Then: PROJECT_ROOT is backend/, the settings module is backend.settings and it is importable
        """
        repo, project = _monorepo()
        (project / "settings.py").write_text("from lex_app.settings import *\n")
        seen = _fresh(_PROBE.format(start="import lex.bin.lex"), project)
        self.assertEqual(seen["project_root"], project.as_posix(), "the CLI must take backend/ as the project")
        self.assertEqual(seen["module"], "backend.settings", "the CLI must use the project's settings.py")
        self.assertTrue(seen["importable"], "backend.settings must be importable without PYTHONPATH")

    # -- 1.406 ---------------------------------------------------------
    def test_1_406_the_reflex_bootstrap_picks_the_same_settings(self) -> None:
        """
        Scenario 1.406: a process the CLI does not start resolves the same settings.
        Given: the monorepo project with a settings.py and no variables set
        When: prepare_environment() runs in backend/ (as the Reflex app module does)
        Then: the same settings module as the CLI, importable
        """
        repo, project = _monorepo()
        (project / "settings.py").write_text("from lex_app.settings import *\n")
        start = "from lex.tools.django_bootstrap import prepare_environment\nprepare_environment()"
        seen = _fresh(_PROBE.format(start=start), project)
        self.assertEqual(seen["module"], "backend.settings", "Reflex workers must use the project's settings too")
        self.assertTrue(seen["importable"], "backend.settings must be importable in the Reflex worker")

    # -- 1.407 ---------------------------------------------------------
    def test_1_407_an_explicit_settings_module_still_wins(self) -> None:
        """
        Scenario 1.407: DJANGO_SETTINGS_MODULE set by the caller is kept.
        Given: the monorepo project with a settings.py and DJANGO_SETTINGS_MODULE=lex_app.settings
        When: the CLI module starts
        Then: lex_app.settings stays
        """
        repo, project = _monorepo()
        (project / "settings.py").write_text("from lex_app.settings import *\n")
        start = "import os\nos.environ['DJANGO_SETTINGS_MODULE'] = 'lex_app.settings'\nimport lex.bin.lex"
        seen = _fresh(_PROBE.format(start=start), project)
        self.assertEqual(seen["module"], "lex_app.settings", "an explicit DJANGO_SETTINGS_MODULE must not be replaced")

    # -- 1.408 ---------------------------------------------------------
    def test_1_408_child_processes_can_import_the_project_settings(self) -> None:
        """
        Scenario 1.408: processes the CLI starts import the project's settings too.
        Given: the CLI module started in backend/ with a settings.py
        When: it starts a child interpreter with its own environment (as a reloading server or worker does)
        Then: the child finds backend.settings
        """
        repo, project = _monorepo()
        (project / "settings.py").write_text("from lex_app.settings import *\n")
        start = (
            "import subprocess, sys\nimport lex.bin.lex\n"
            "child = subprocess.run([sys.executable, '-c', "
            "'import importlib.util, os; print(importlib.util.find_spec(os.environ[\"DJANGO_SETTINGS_MODULE\"]) is not None)'],"
            " capture_output=True, text=True, cwd='/')\n"
            "assert child.stdout.strip() == 'True', child.stderr or child.stdout"
        )
        seen = _fresh(_PROBE.format(start=start), project)
        self.assertEqual(seen["module"], "backend.settings", "the child must inherit the project's settings module")
