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

    def test_1_393_the_settings_warning_policy_holds_for_the_tests(self):
        """Scenario 1.393 (third half): the warnings a run reports are the ones `lex
        pytest` reported. The settings install lex's warning policy
        (LEX_SUPPRESS_WARNINGS) when imported; `lex pytest` imported them before pytest
        started, so its tests ran under that policy, and a plain run must too."""
        quiet = _run_pytest(f"{TARGETS_REL}/warning_target.py")
        loud = _run_pytest(
            f"{TARGETS_REL}/warning_target.py", extra_env={"LEX_SUPPRESS_WARNINGS": "False"}
        )

        self.assertEqual(quiet.returncode, 0, _output(quiet))
        self.assertNotIn("lex-1aw-warning-probe", quiet.stdout, _output(quiet))
        self.assertEqual(loud.returncode, 0, _output(loud))
        self.assertIn("lex-1aw-warning-probe", loud.stdout, _output(loud))

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


# The DOCKER-COMPOSE target with its variables removed names host "envvar_not_existing".
# Django's start-up never connects in this repository, so only an actual database
# setup fails against it.
_UNREACHABLE_DATABASE = {"DATABASE_DEPLOYMENT_TARGET": "DOCKER-COMPOSE"}
_UNREACHABLE_DROPPED = ("DATABASE_DOMAIN", "DATABASE_NAME", "POSTGRES_PASSWORD")


class _FakePluginManager:
    """Just the three calls pytest_configure() makes on config.pluginmanager."""

    def __init__(self, active: frozenset[str] = frozenset()):
        self._active = set(active)
        self.registered: list = []

    def hasplugin(self, name: str) -> bool:
        return name in self._active

    def get_plugins(self) -> list:
        return list(self.registered)

    def register(self, plugin, name=None):
        self.registered.append(plugin)


def _fake_config(*, collectonly: bool = False, active: frozenset[str] = frozenset()):
    """A config holding what pytest_load_initial_conftests() stashes for this repo."""
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
        result = _run_pytest(
            "--collect-only", "-q", f"{TARGETS_REL}/django_db_target.py",
            extra_env=_UNREACHABLE_DATABASE, drop=_UNREACHABLE_DROPPED,
        )

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
        result = _run_pytest(
            f"{TARGETS_REL}/django_db_target.py",
            extra_env=_UNREACHABLE_DATABASE, drop=_UNREACHABLE_DROPPED,
        )

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
        config = _fake_config(active=frozenset({"django"}))

        with self.assertRaises(pytest.UsageError) as raised:
            pytest_plugin.pytest_configure(config)

        self.assertIn("-p no:django", str(raised.exception))
        self.assertEqual(config.pluginmanager.registered, [], "nothing may be registered first")


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
        # Through PYTEST_PLUGINS, not -p: pytest registers -p plugins before entry-point
        # plugins and PYTEST_PLUGINS after them, the way it registers conftests. Hooks
        # are called last-registered first, so only this order shows `tryfirst` working.
        result = _run_pytest(
            "-s", f"{TARGETS_REL}/django_db_target.py",
            extra_env={"PYTEST_PLUGINS": "lex.test_project.tests.init.pytest_plugin_targets.copied_setup_plugin"},
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
              and sets the test environment and database up itself no more
        """
        from click.testing import CliRunner

        from lex.bin.lex import lex

        # The database and environment calls are stubbed so that a `pytest_cmd` that
        # still makes them cannot touch the suite's own test database or environment.
        with tempfile.TemporaryDirectory() as tmp:
            config = self._config(Path(tmp))
            with patch("lex.bin.lex.os.chdir"), patch("lex.bin.lex._bootstrap_django"), \
                    patch("lex.bin.lex._has_explicit_pytest_target", return_value=False), \
                    patch("lex.tools.test_groups.resolve_config", return_value=config), \
                    patch("django.test.utils.setup_test_environment") as setup, \
                    patch("django.test.utils.teardown_test_environment"), \
                    patch("django.test.runner.DiscoverRunner.setup_databases") as setup_databases, \
                    patch("django.test.runner.DiscoverRunner.teardown_databases"), \
                    patch("pytest.main", return_value=0) as pytest_main:
                result = CliRunner().invoke(lex, ["pytest", "-q"])

        self.assertEqual(result.exit_code, 0, result.output)
        args, kwargs = pytest_main.call_args
        self.assertEqual(args[0], ["-p", "no:lex", "Tests", "-q"])
        self.assertIs(kwargs["plugins"][0], pytest_plugin)
        setup.assert_not_called()
        setup_databases.assert_not_called()

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
            beta_exists = (root / ".run" / "Tests_beta.run.xml").exists()
            launch = json.loads((root / ".vscode" / "launch.json").read_text(encoding="utf-8"))

        self.assertTrue(beta_exists, "every group gets a configuration")
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
