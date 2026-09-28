"""Cluster 1ap: ``lex reflex`` -- the command, its run configuration, and the
bootstrap its worker processes run.

Intent
------
Reflex is the second dashboard framework lex-app serves, beside Streamlit, and
it is started the same way: one ``lex`` command, one IDE run configuration.
What differs is where the app runs. ``lex streamlit`` hosts Streamlit inside the
CLI process after the CLI has set Django up; ``reflex run`` compiles and serves
the app from worker processes that never see the CLI's bootstrap. So:

* ``lex reflex`` must not set Django up itself (the workers do), must run Reflex
  from the project root with an ``rxconfig.py`` that points Reflex at lex-app's
  app module, and must pass every argument through untouched -- ``--help``
  included, which lex must not answer on Reflex's behalf.
* Development needs fixed ports clear of the rest of the lex stack (Keycloak
  must know the callback URL in advance), yet Reflex refuses a run handed a
  port its mode cannot use -- so the defaults are supplied per mode, and a port
  the caller chose, anywhere, always wins.
* Hot reload must watch the project, not the installed lex package, and never
  ``.web/`` -- which every compile rewrites.
* The worker bootstrap must give a worker exactly the environment the CLI gives
  itself: the project's ``.env`` without overriding the process, the settings
  module, ``PROJECT_ROOT``, the package directory on ``sys.path``.

A regression here reads as "the dashboard won't start", "sign-in loops on a
redirect URI mismatch", or "the backend reloads forever".

Cluster 1ap -- scenarios 1.348-1.356. Type: U.
Covers: lex/bin/lex.py (``reflex_cmd``, ``_ensure_reflex_config``,
``REFLEX_CONFIG_TEMPLATE``, ``_reflex_hot_reload_paths``,
``_resolve_reflex_port_flags``, ``_SKIP_BOOTSTRAP_COMMANDS``),
generate_pycharm_configs.py (``RUN_CONFIGS``), lex/tools/django_bootstrap.py,
lex/tools/project_root.py (``load_project_env_file``).
Run: python -m lex pytest lex/test_project/tests/init/test_1ap_reflex_cli.py -v
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch
from xml.etree import ElementTree

import pytest
from click.testing import CliRunner

pytestmark = pytest.mark.init

#: Variables the command reads, cleared so the host's own cannot leak in.
_REFLEX_ENV = (
    "REFLEX_HOT_RELOAD_OVERRIDE_PATHS",
    "REFLEX_FRONTEND_PORT",
    "REFLEX_BACKEND_PORT",
    "REFLEX_BACKEND_ONLY",
    "REFLEX_FRONTEND_ONLY",
)


class _ReflexProject(TestCase):
    """A temporary project root, and ``lex reflex`` invoked against it.

    ``reflex.reflex.cli.main`` -- the Reflex CLI -- is the external boundary:
    it is replaced by a recorder that captures the arguments and the working
    directory it would have run with. Everything on lex's side runs for real,
    including the ``rxconfig.py`` the command writes and Reflex's own
    ``get_config()`` loading it.
    """

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.project_root = Path(tmp.name).resolve()

        root_patch = patch("lex.bin.lex.PROJECT_ROOT_DIR", self.project_root)
        root_patch.start()
        self.addCleanup(root_patch.stop)

        env = patch.dict(os.environ, {"CI": "true"})
        env.start()
        self.addCleanup(env.stop)
        for name in _REFLEX_ENV:
            os.environ.pop(name, None)

    def invoke(self, *args: str):
        """Run ``lex reflex <args>``; return (result, recorded Reflex call)."""
        import reflex.reflex
        from reflex_base.registry import RegistrationContext

        from lex.bin.lex import lex as lex_cli

        recorded: dict = {}

        def fake_reflex_main(args=None, prog_name=None, **_kwargs):
            recorded["args"] = list(args)
            recorded["prog_name"] = prog_name
            recorded["cwd"] = Path.cwd().resolve()
            recorded["hot_reload"] = os.environ.get("REFLEX_HOT_RELOAD_OVERRIDE_PATHS")

        # The command chdirs into the project; chdir() puts the test back.
        # Reflex caches the config it loads on the active registration
        # context, so the load happens in a fork of it, not in the shared one.
        with contextlib.chdir(Path.cwd()), RegistrationContext.ensure_context().fork(), \
                patch.object(reflex.reflex.cli, "main", fake_reflex_main):
            result = CliRunner().invoke(lex_cli, ["reflex", *args], catch_exceptions=False)
        return result, recorded


class TestCluster01ap_TheCommand(_ReflexProject):
    """`lex reflex` is the Reflex CLI, run where Reflex needs to run."""

    # -- 1.348 ---------------------------------------------------------
    def test_1_348_no_arguments_means_reflex_run_without_a_cli_bootstrap(self) -> None:
        """
        Scenario 1.348: ``lex reflex`` alone is ``reflex run``, and the CLI never sets Django up.
        Given: a project with no rxconfig.py.
        When:  ``lex reflex`` runs with no arguments.
        Then:  Reflex receives ``run`` first, under the name ``lex reflex``, and
               the command is one ``main()`` hands to click without calling
               ``django.setup()`` -- the workers do that, where the app runs.
        """
        from lex.bin.lex import _SKIP_BOOTSTRAP_COMMANDS, _should_skip_django_bootstrap, lex

        self.assertIn("reflex", lex.commands, "`reflex` must be an explicit lex command")
        self.assertIn(
            "reflex", _SKIP_BOOTSTRAP_COMMANDS,
            "the CLI process is not where the Reflex app runs; it must not set Django up",
        )
        self.assertTrue(_should_skip_django_bootstrap("reflex"))

        result, recorded = self.invoke()

        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(recorded["args"][0], "run", f"default must be `reflex run`, got {recorded}")
        self.assertEqual(recorded["prog_name"], "lex reflex")

    # -- 1.349 ---------------------------------------------------------
    def test_1_349_arguments_reach_reflex_untouched_help_included(self) -> None:
        """
        Scenario 1.349: every argument is Reflex's, ``--help`` included.
        Given: ``lex reflex --help`` and ``lex reflex export --no-zip``.
        When:  each runs.
        Then:  Reflex receives exactly those arguments -- lex answers no
               ``--help`` of its own -- and a command that acts on no project
               (``--help``, ``login``) writes no rxconfig.py.
        """
        result, recorded = self.invoke("--help")
        self.assertEqual(result.exit_code, 0, msg=result.output)
        self.assertEqual(recorded.get("args"), ["--help"], "lex must not answer --help for Reflex")
        self.assertNotIn("Usage: lex reflex", result.output)
        self.assertFalse(
            (self.project_root / "rxconfig.py").exists(),
            "asking for help must not write files into the project",
        )

        _, recorded = self.invoke("login")
        self.assertEqual(recorded.get("args"), ["login"])
        self.assertFalse((self.project_root / "rxconfig.py").exists())

        _, recorded = self.invoke("export", "--no-zip")
        self.assertEqual(
            recorded.get("args"), ["export", "--no-zip"],
            "only `run` gets port flags; other subcommands pass through verbatim",
        )

    # -- 1.350 ---------------------------------------------------------
    def test_1_350_rxconfig_is_written_once_and_reflex_runs_in_the_project(self) -> None:
        """
        Scenario 1.350: the first run writes rxconfig.py; later runs leave it alone.
        Given: a project with no rxconfig.py, then one the project edited.
        When:  ``lex reflex`` runs each time.
        Then:  the written file loads the project's .env before importing
               ``lex_config`` and returns ``lex_config()``; an existing file
               is never touched; Reflex runs with the project root as its cwd.
        """
        from lex.bin.lex import REFLEX_CONFIG_TEMPLATE

        result, recorded = self.invoke()
        config = self.project_root / "rxconfig.py"

        self.assertTrue(config.is_file(), "Reflex reads rxconfig.py from the project root")
        self.assertIn("rxconfig.py", result.output, "creating the file must be reported")
        text = config.read_text(encoding="utf-8")
        self.assertEqual(text, REFLEX_CONFIG_TEMPLATE)
        self.assertLess(
            text.index("prepare_environment()"), text.index("from lex.lex_app.reflex.config"),
            "the .env must be loaded before importing lex.lex_app evaluates the settings",
        )
        self.assertIn("config = lex_config()", text)
        compile(text, "rxconfig.py", "exec")
        self.assertEqual(recorded["cwd"], self.project_root, "Reflex must run in the project root")

        edited = text + "\n# the project's own edit\n"
        config.write_text(edited, encoding="utf-8")
        result, _ = self.invoke()
        self.assertEqual(config.read_text(encoding="utf-8"), edited, "an existing rxconfig.py is the project's")
        self.assertNotIn("(created)", result.output)


class TestCluster01ap_HotReload(_ReflexProject):
    """Hot reload watches the project -- never .web/, never the installed package."""

    # -- 1.351 ---------------------------------------------------------
    def test_1_351_hot_reload_watches_the_projects_own_entries(self) -> None:
        """
        Scenario 1.351: ``REFLEX_HOT_RELOAD_OVERRIDE_PATHS`` names the project's entries.
        Given: a project holding models, a structure file, .web/, a .venv, a
               virtualenv under a plain name, migrations/ and __pycache__/.
        When:  ``lex reflex`` runs.
        Then:  the override lists the project's own entries by relative name,
               joined with ':' -- not .web/, not hidden, not generated, not a
               virtualenv whatever it is called; an override the environment
               already sets is kept as it is.
        """
        for directory in ("Input", ".web", ".venv", "migrations", "__pycache__", "node_modules"):
            (self.project_root / directory).mkdir()
        (self.project_root / "tooling").mkdir()
        (self.project_root / "tooling" / "pyvenv.cfg").write_text("home = /usr\n")
        (self.project_root / "_reflex_structure.py").write_text("")
        (self.project_root / ".env").write_text("")

        _, recorded = self.invoke()
        watched = recorded["hot_reload"].split(":")

        self.assertIn("Input", watched)
        self.assertIn("_reflex_structure.py", watched)
        self.assertIn("rxconfig.py", watched, "the config the command wrote is the project's too")
        for excluded in (".web", ".venv", ".env", "migrations", "__pycache__", "node_modules", "tooling"):
            self.assertNotIn(excluded, watched, f"{excluded} must not be watched: {watched}")
        self.assertTrue(
            all(not os.path.isabs(name) for name in watched),
            "absolute paths break Reflex's ':' split on Windows drive letters",
        )

        with patch.dict(os.environ, {"REFLEX_HOT_RELOAD_OVERRIDE_PATHS": "custom"}):
            _, recorded = self.invoke()
        self.assertEqual(recorded["hot_reload"], "custom", "an explicit override must win")

    def test_1_351b_an_empty_project_still_watches_something_real(self) -> None:
        """
        Scenario 1.351: the override is never empty.
        Given: a project root with nothing but hidden entries.
        When:  the watch list is computed.
        Then:  it still names a real file -- an empty value would resolve to
               the whole project root, .web/ included.
        """
        from lex.bin.lex import _reflex_hot_reload_paths

        (self.project_root / ".web").mkdir()
        self.assertEqual(_reflex_hot_reload_paths(self.project_root), "rxconfig.py")


class TestCluster01ap_Ports(_ReflexProject):
    """Fixed default ports, per run mode, and the caller's always win."""

    # -- 1.352 ---------------------------------------------------------
    def test_1_352_development_gets_both_defaults_unless_the_caller_chose(self) -> None:
        """
        Scenario 1.352: in development each missing port is supplied; a chosen one wins.
        Given: ``lex reflex run`` with no ports, then with ports chosen by
               flag (both spellings), by environment, and in rxconfig.py.
        When:  the port flags are resolved.
        Then:  the defaults are 8502/8503 -- beside Streamlit's 8501, clear of
               the React (3000) and Django (8000) dev servers -- and a port the
               caller chose anywhere is never re-appended over theirs.
        """
        from lex.bin.lex import _resolve_reflex_port_flags

        self.assertEqual(
            _resolve_reflex_port_flags(["run"]),
            ["--frontend-port", "8502", "--backend-port", "8503"],
        )
        self.assertEqual(
            _resolve_reflex_port_flags(["run", "--frontend-port", "9000"]),
            ["--backend-port", "8503"],
        )
        self.assertEqual(
            _resolve_reflex_port_flags(["run", "--backend-port=9001"]),
            ["--frontend-port", "8502"],
        )
        with patch.dict(os.environ, {"REFLEX_BACKEND_PORT": "9002"}):
            self.assertEqual(_resolve_reflex_port_flags(["run"]), ["--frontend-port", "8502"])
        self.assertEqual(
            _resolve_reflex_port_flags(["run"], configured_frontend=9100),
            ["--backend-port", "8503"],
        )

        # End to end: a port rxconfig.py chooses survives the command.
        (self.project_root / "rxconfig.py").write_text(
            "from lex.lex_app.reflex.config import lex_config\n"
            "config = lex_config(frontend_port=9100)\n",
            encoding="utf-8",
        )
        _, recorded = self.invoke("run")
        self.assertNotIn("--frontend-port", recorded["args"], recorded["args"])
        self.assertEqual(recorded["args"][-2:], ["--backend-port", "8503"])

    # -- 1.353 ---------------------------------------------------------
    def test_1_353_each_mode_gets_only_a_port_it_can_use(self) -> None:
        """
        Scenario 1.353: backend-only, frontend-only and prod runs are handed one port.
        Given: the run modes Reflex validates.
        When:  the port flags are resolved.
        Then:  ``--backend-only`` gets only a backend port and
               ``--frontend-only`` only a frontend one (Reflex refuses the
               other); ``--env prod``/``preview`` serve both on one port, so get
               one -- and none once the caller chose any; nothing but ``run``
               gets ports at all.
        """
        from lex.bin.lex import _resolve_reflex_port_flags

        cases = {
            ("run", "--backend-only"): ["--backend-port", "8503"],
            ("run", "--frontend-only"): ["--frontend-port", "8502"],
            ("run", "--env", "prod"): ["--frontend-port", "8502"],
            ("run", "--env=PROD"): ["--frontend-port", "8502"],
            ("run", "--env", "preview"): ["--frontend-port", "8502"],
            ("run", "--env", "prod", "--backend-only"): ["--backend-port", "8503"],
            ("run", "--env", "prod", "--backend-port", "80"): [],
            ("export",): [],
            ("compile",): [],
        }
        for args, expected in cases.items():
            with self.subTest(args=args):
                self.assertEqual(_resolve_reflex_port_flags(list(args)), expected)

        with patch.dict(os.environ, {"REFLEX_BACKEND_ONLY": "true"}):
            self.assertEqual(
                _resolve_reflex_port_flags(["run"]), ["--backend-port", "8503"],
                "the mode can come from Reflex's own environment variable too",
            )

        _, recorded = self.invoke("run", "--env", "prod")
        self.assertEqual(recorded["args"], ["run", "--env", "prod", "--frontend-port", "8502"])


class TestCluster01ap_RunConfiguration(TestCase):
    """The IDE runs `lex reflex run`, with the project's .env, in both formats."""

    # -- 1.354 ---------------------------------------------------------
    def test_1_354_setup_generates_a_reflex_configuration_for_both_ides(self) -> None:
        """
        Scenario 1.354: ``lex setup`` scaffolds a "Reflex" run configuration.
        Given: a neutral shell (no IDE marker), so both formats are generated.
        When:  ``lex setup`` runs.
        Then:  PyCharm's Reflex.run.xml and VS Code's "LEX: Reflex" both run
               the ``lex`` module with ``reflex run`` from the project root,
               loading its .env -- the same shape as the Streamlit entry.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / ".env").write_text("", encoding="utf-8")
            markers = {"TERM_PROGRAM", "VSCODE_PID", "TERMINAL_EMULATOR", "PYCHARM_HOSTED",
                       "IDEA_INITIAL_DIRECTORY", "VSCODE_CWD", "VSCODE_INJECTION",
                       "VSCODE_IPC_HOOK", "VSCODE_IPC_HOOK_CLI"}
            environment = {k: v for k, v in os.environ.items() if k not in markers}
            with patch("lex.bin.lex.find_project_root", return_value=root.as_posix()), \
                    patch.dict(os.environ, environment, clear=True):
                from lex.bin.lex import setup

                result = CliRunner().invoke(setup, [], catch_exceptions=False)
            self.assertEqual(result.exit_code, 0, msg=result.output)

            run_file = root / ".run" / "Reflex.run.xml"
            self.assertTrue(run_file.is_file(), "PyCharm must get a Reflex configuration")
            configuration = ElementTree.parse(run_file).getroot().find(".//configuration")
            options = {o.attrib["name"]: o.attrib.get("value", "") for o in configuration.findall("option")}
            self.assertEqual(configuration.attrib["name"], "Reflex")
            self.assertEqual(options["SCRIPT_NAME"], "lex")
            self.assertEqual(options["MODULE_MODE"], "true")
            self.assertEqual(options["PARAMETERS"], "reflex run")
            self.assertEqual(options["WORKING_DIRECTORY"], str(root))
            self.assertEqual(options["ENV_FILES"], str(root / ".env"))

            launch = json.loads((root / ".vscode" / "launch.json").read_text(encoding="utf-8"))
            entry = next(c for c in launch["configurations"] if c["name"] == "LEX: Reflex")
            self.assertEqual(entry["module"], "lex")
            self.assertEqual(entry["args"], ["reflex", "run"])
            self.assertEqual(entry["cwd"], "${workspaceFolder}")
            self.assertEqual(entry["envFile"], "${workspaceFolder}/.env")


class TestCluster01ap_WorkerBootstrap(TestCase):
    """A Reflex worker gets the environment the CLI gives itself."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        saved_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), saved_path))

    # -- 1.355 ---------------------------------------------------------
    def test_1_355_prepare_environment_mirrors_the_cli(self) -> None:
        """
        Scenario 1.355: a worker's environment is the CLI's.
        Given: a project whose .env sets two variables, one of which the
               process already has, and no DJANGO_SETTINGS_MODULE.
        When:  ``prepare_environment()`` runs.
        Then:  the new variable is loaded and the existing one kept; the
               settings module, PROJECT_ROOT and LEX_APP_PACKAGE_ROOT are set;
               the lex package directory -- what makes ``lex_app.settings``
               importable -- is on sys.path; and PROJECT_ROOT decides the
               project over the working directory.
        """
        from lex.tools.django_bootstrap import LEX_PACKAGE_DIR, prepare_environment

        (self.root / ".env").write_text(
            "LEX_1AP_FROM_FILE=file\nLEX_1AP_ALREADY_SET=file\n", encoding="utf-8"
        )
        environment = {
            k: v for k, v in os.environ.items()
            if k not in {"DJANGO_SETTINGS_MODULE", "LEX_APP_PACKAGE_ROOT"}
        }
        environment.update({"PROJECT_ROOT": self.root.as_posix(), "LEX_1AP_ALREADY_SET": "process"})
        sys.path[:] = [p for p in sys.path if p not in (LEX_PACKAGE_DIR.as_posix(), str(LEX_PACKAGE_DIR))]

        expected = {
            "LEX_1AP_FROM_FILE": "file",
            "LEX_1AP_ALREADY_SET": "process",  # .env must never override the process
            "DJANGO_SETTINGS_MODULE": "lex_app.settings",
            "PROJECT_ROOT": self.root.as_posix(),
            "LEX_APP_PACKAGE_ROOT": LEX_PACKAGE_DIR.as_posix(),
        }
        with patch.dict(os.environ, environment, clear=True):
            returned = prepare_environment()
            self.assertEqual(returned, self.root)
            self.assertEqual({name: os.environ.get(name) for name in expected}, expected)
            self.assertIn(LEX_PACKAGE_DIR.as_posix(), sys.path)
            self.assertTrue((LEX_PACKAGE_DIR / "lex_app" / "settings.py").is_file())

    def test_1_355b_setup_django_is_a_no_op_once_django_is_ready(self) -> None:
        """
        Scenario 1.355: ``setup_django()`` never sets Django up twice.
        Given: a process where Django is already set up (this one).
        When:  ``setup_django()`` runs.
        Then:  ``django.setup()`` is not called again -- a second setup would
               re-run every ``AppConfig.ready()``.
        """
        from lex.tools.django_bootstrap import setup_django

        with patch("django.setup") as django_setup:
            setup_django()
        django_setup.assert_not_called()

    # -- 1.356 ---------------------------------------------------------
    def test_1_356_the_env_file_is_parsed_as_the_cli_always_parsed_it(self) -> None:
        """
        Scenario 1.356: one .env parser, shared by the CLI and the workers.
        Given: a .env with comments, blanks, quoted values and '=' in a value.
        When:  it is loaded.
        Then:  comments and blank lines are skipped, surrounding quotes
               stripped, a later '=' kept in the value -- and the CLI's
               ``_load_project_env_file`` is this same function.
        """
        from lex.bin.lex import _load_project_env_file
        from lex.tools.project_root import load_project_env_file

        self.assertIs(_load_project_env_file, load_project_env_file)
        (self.root / ".env").write_text(
            "# comment\n\nLEX_1AP_QUOTED=\"a b\"\nLEX_1AP_SINGLE='c'\n"
            "LEX_1AP_URL=postgres://u:p@h/db?x=1\nnot a pair\n",
            encoding="utf-8",
        )
        expected = {"LEX_1AP_QUOTED": "a b", "LEX_1AP_SINGLE": "c", "LEX_1AP_URL": "postgres://u:p@h/db?x=1"}
        with patch.dict(os.environ, {}, clear=False):
            for name in expected:
                os.environ.pop(name, None)
            load_project_env_file(self.root)
            self.assertEqual({name: os.environ.get(name) for name in expected}, expected)
        load_project_env_file(self.root / "missing")  # no .env: nothing to do, no error
