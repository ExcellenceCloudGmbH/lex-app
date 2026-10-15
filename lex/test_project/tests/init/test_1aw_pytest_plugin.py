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


def _run_pytest(
    *args: str,
    cwd: Path = REPO_ROOT,
    extra_env: dict | None = None,
    drop: tuple[str, ...] = (),
) -> subprocess.CompletedProcess:
    """`python -m pytest ARGS` the way an IDE starts it: no `lex` CLI, none of its env.

    ``drop`` removes variables outright: a variable set to "" is not the same as an
    unset one to ``os.getenv(name, default)``.
    """
    with tempfile.TemporaryDirectory() as tmp:
        unwanted = {*_SETUP_VARS, *drop}
        env = {key: value for key, value in os.environ.items() if key not in unwanted}
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
                loaded = os.environ.get("LEX_1AW_PROBE")

        self.assertEqual(loaded, "from-this-root", "the given root's .env must be loaded")
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
