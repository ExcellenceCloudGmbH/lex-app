"""Startup hardening: nested projects, a broken lex_config.py, cache tables, CORS origins.

Intent
------
* A Lex project in a subfolder of a git repository (``lex_config.py`` there,
  a web client beside it) runs with ``python -m lex <cmd>`` and no extra
  environment variables: the nearest folder holding ``lex_config.py`` is the
  project root, before the git toplevel. Projects without one keep the git
  toplevel. The settings module stays ``lex_app.settings``.
* A ``lex_config.py`` that fails to import, or sets one of its keys
  (``INITIAL_DATA``, ``PROJECT_GROUPS``, ``DEFAULT_SERIALIZER_NAME``,
  ``TAB_DISPLAY_NAMES``) to the wrong type, stops startup with a clear error
  instead of silently running with defaults.
* A fresh database gets its cache tables automatically: after every
  ``migrate`` and before the initial-data load, so the load no longer fails
  with ``relation "default_cache" does not exist``. Idempotent.
* ``LEX_CORS_ALLOWED_ORIGINS`` (comma-separated) replaces the permissive CORS
  defaults when set; unset, CORS is unchanged.

Cluster 1av — scenarios 1.386–1.393. Type: U (1.390–1.391 I).
Covers: lex/tools/project_root.py (find_project_root), lex/bin/lex.py (module
start), lex/core/config.py (load_error, raw_values, validate_project_config),
lex/lex_app/apps.py (_validate_project_configuration, async_ready),
lex/helpers/cache_tables.py (create_cache_tables*), lex/core/apps.py
(post_migrate), lex/lex_app/settings.py (LEX_CORS_ALLOWED_ORIGINS).
Run: python -m lex pytest lex/test_project/tests/init/test_1av_startup_hardening.py -v
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import connection
from django.db.models.signals import post_migrate
from django.test import SimpleTestCase, TransactionTestCase

from lex.core.config import LexProjectConfig, validate_project_config
from lex.lex_app import apps as lex_apps
from lex.tools import project_root as project_root_module
from lex.tools.project_root import find_project_root

pytestmark = pytest.mark.init

#: The checkout holding the ``lex`` package, so a fresh interpreter imports this lex.
_CHECKOUT = Path(project_root_module.__file__).resolve().parents[2]

#: Variables a fresh interpreter must not inherit from the test process.
_CLEARED = ("DJANGO_SETTINGS_MODULE", "PROJECT_ROOT", "PYTHONPATH", "GIT_CEILING_DIRECTORIES", "LEX_CORS_ALLOWED_ORIGINS")


def _fresh(code: str, cwd: Path, **env_overrides) -> dict:
    """Run ``code`` in a fresh interpreter in ``cwd``; it prints one JSON value last."""
    env = {k: v for k, v in os.environ.items() if k not in _CLEARED}
    env["PYTHONPATH"] = _CHECKOUT.as_posix()
    env.update(env_overrides)
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=cwd, env=env, capture_output=True, text=True, timeout=180
    )
    assert result.returncode == 0, f"fresh interpreter failed:\n{result.stderr[-3000:]}"
    return json.loads(result.stdout.strip().splitlines()[-1])


def _monorepo() -> tuple[Path, Path]:
    """A git repository with a Lex project in ``backend/`` and a web client beside it."""
    repo = Path(tempfile.mkdtemp(prefix="cluster1av_")).resolve()
    subprocess.run(["git", "init", "-q", repo.as_posix()], check=True)
    (repo / "web").mkdir()
    (repo / "web" / "package.json").write_text("{}")
    project = repo / "backend"
    (project / "Inputs").mkdir(parents=True)
    (project / "lex_config.py").write_text('PROJECT_GROUPS = ["backend"]\n')
    return repo, project


class TestCluster01av_NestedProjectRoot(SimpleTestCase):
    """D3: the project root of a Lex project nested in a repository."""

    # -- 1.386 ---------------------------------------------------------
    def test_1_386_lex_config_marks_the_root_below_git(self) -> None:
        """
        Scenario 1.386: the nearest folder with lex_config.py is the project root.
        Given: a git repository with a Lex project in backend/, and the same
               repository after lex_config.py is removed
        When: the root is resolved from backend/ and from backend/Inputs
        Then: backend/ both times; without lex_config.py the git toplevel, as before.
        """
        repo, project = _monorepo()
        self.assertEqual(Path(find_project_root(project)), project)
        self.assertEqual(Path(find_project_root(project / "Inputs")), project)
        (project / "lex_config.py").unlink()
        self.assertEqual(Path(find_project_root(project)), repo)

    # -- 1.387 ---------------------------------------------------------
    def test_1_387_the_cli_starts_a_nested_project_without_variables(self) -> None:
        """
        Scenario 1.387: python -m lex in a nested project needs no environment variables.
        Given: the monorepo project and no PROJECT_ROOT, PYTHONPATH,
               GIT_CEILING_DIRECTORIES or DJANGO_SETTINGS_MODULE
        When: the CLI module starts in backend/Inputs
        Then: PROJECT_ROOT is backend/ and the settings module is lex_app.settings
              (no project settings.py is loaded).
        """
        repo, project = _monorepo()
        (project / "settings.py").write_text("raise RuntimeError('a project settings.py must not be loaded')\n")
        seen = _fresh(
            "import json, os\nimport lex.bin.lex\n"
            "print(json.dumps([os.environ['PROJECT_ROOT'], os.environ['DJANGO_SETTINGS_MODULE']]))",
            project / "Inputs",
        )
        self.assertEqual(seen, [project.as_posix(), "lex_app.settings"])

    # -- 1.393 ---------------------------------------------------------
    def test_1_393_the_search_stops_below_home(self) -> None:
        """
        Scenario 1.393: lex_config.py is looked for only below the user's home.
        Given: a folder directly below a temporary "home" holding lex_config.py,
               and a folder with no marker at all
        When: the root is resolved from inside them
        Then: the lex_config.py folder; the starting folder for the marker-less one
              (never the home directory itself).
        """
        home = Path(tempfile.mkdtemp(prefix="cluster1av_home_")).resolve()
        project = home / "proj"
        (project / "sub").mkdir(parents=True)
        (project / "lex_config.py").write_text("")
        bare = home / "bare" / "deep"
        bare.mkdir(parents=True)
        (home / "lex_config.py").write_text("")  # a stray file in home must never win
        with mock.patch.object(Path, "home", return_value=home), \
                mock.patch.object(project_root_module.subprocess, "run", side_effect=OSError("no git")):
            self.assertEqual(Path(find_project_root(project / "sub")), project)
            self.assertEqual(Path(find_project_root(bare)), bare)


class TestCluster01av_ConfigValidation(SimpleTestCase):
    """D4: a broken lex_config.py stops startup."""

    def _project(self, source):
        root = tempfile.mkdtemp(prefix="cluster1av_cfg_")
        if source is not None:
            Path(root, "lex_config.py").write_text(source)
        patcher = mock.patch.dict(os.environ, {"PROJECT_ROOT": root})
        patcher.start()
        self.addCleanup(patcher.stop)

    # -- 1.388 ---------------------------------------------------------
    def test_1_388_a_config_that_fails_to_load_stops_startup(self) -> None:
        """
        Scenario 1.388: an exception inside lex_config.py is not swallowed.
        Given: a lex_config.py that raises while executing
        When: lex validates the configuration as ready() does
        Then: ImproperlyConfigured naming lex_config.py and the original error.
        """
        self._project('INITIAL_DATA = "data.json"\nraise RuntimeError("cluster 1av boom")\n')
        with self.assertRaisesMessage(ImproperlyConfigured, "cluster 1av boom"):
            lex_apps._validate_project_configuration()
        with self.assertRaisesMessage(ImproperlyConfigured, "lex_config.py could not be loaded"):
            validate_project_config()

    # -- 1.389 ---------------------------------------------------------
    def test_1_389_wrong_types_stop_startup_and_valid_configs_pass(self) -> None:
        """
        Scenario 1.389: each 2.3.5 key is checked; correct or absent configs pass.
        Given: one wrong value at a time for INITIAL_DATA, PROJECT_GROUPS,
               DEFAULT_SERIALIZER_NAME and TAB_DISPLAY_NAMES (also under their
               legacy names); then a complete valid file; then no file
        When: lex validates the configuration
        Then: ImproperlyConfigured naming the key for each wrong value; no error
              for the valid file (its values are loaded) or the missing one.
        """
        cases = {
            "INITIAL_DATA = 42": "INITIAL_DATA",
            'PROJECT_GROUPS = "admins"': "PROJECT_GROUPS",
            "azure_groups = [1, 2]": "PROJECT_GROUPS",
            'DEFAULT_SERIALIZER_NAME = "  "': "DEFAULT_SERIALIZER_NAME",
            'TAB_DISPLAY_NAMES = {"invoice": "History"}': "TAB_DISPLAY_NAMES",
            "tab_display_names = ['history']": "TAB_DISPLAY_NAMES",
        }
        for source, key in cases.items():
            with self.subTest(key=key, source=source):
                self._project(source + "\n")
                with self.assertRaisesMessage(ImproperlyConfigured, key):
                    validate_project_config()
        self._project(
            'INITIAL_DATA = "Tests/data.json"\nPROJECT_GROUPS = ["a", "b"]\n'
            'DEFAULT_SERIALIZER_NAME = "full"\n'
            'TAB_DISPLAY_NAMES = {"__default__": {"history_tab": "Versions"}}\n'
        )
        config = validate_project_config()
        self.assertEqual(
            (config.initial_data, config.groups, config.default_serializer_name),
            ("Tests/data.json", ["a", "b"], "full"),
        )
        self._project(None)
        self.assertFalse(validate_project_config()._loaded)


def _cache_tables():
    with connection.cursor() as cursor:
        names = set(connection.introspection.table_names(cursor))
    return {"default_cache", "oidc_cache"} & names


def _drop_cache_tables():
    with connection.cursor() as cursor:
        cursor.execute("DROP TABLE IF EXISTS default_cache")
        cursor.execute("DROP TABLE IF EXISTS oidc_cache")


class TestCluster01av_CacheTables(TransactionTestCase):
    """E2: the database cache tables exist without a manual createcachetable."""

    def setUp(self) -> None:
        from django.core.management import call_command

        # Whatever happens here, later tests get their cache tables back.
        self.addCleanup(call_command, "createcachetable", verbosity=0)

    # -- 1.390 ---------------------------------------------------------
    def test_1_390_migrate_creates_the_cache_tables(self) -> None:
        """
        Scenario 1.390: post_migrate creates the cache tables, and again is harmless.
        Given: a database without default_cache and oidc_cache
        When: migrate's post_migrate signal is sent for lex's core app, twice
        Then: both tables exist after the first and the second send raises nothing.
        """
        _drop_cache_tables()
        self.assertEqual(_cache_tables(), set())
        core = apps.get_app_config("core")
        for _ in range(2):
            post_migrate.send(
                sender=core, app_config=core, verbosity=0, interactive=False, using="default",
                apps=apps, plan=[],
            )
        self.assertEqual(_cache_tables(), {"default_cache", "oidc_cache"})

    # -- 1.391 ---------------------------------------------------------
    def test_1_391_initial_data_start_creates_the_cache_tables_first(self) -> None:
        """
        Scenario 1.391: the server start creates the cache tables before the initial-data load.
        Given: a database without cache tables, a project whose lex_config.py names
               initial data, and a server started by `lex start` (uvicorn)
        When: the initial-data check (async_ready) runs
        Then: the cache tables exist by the time it decides whether to load.
        """
        _drop_cache_tables()
        seen = {}

        def decide(test, models):
            seen["tables"] = _cache_tables()
            return False

        config = LexProjectConfig(initial_data="Tests/data.json")
        config._loaded = True
        app_config = apps.get_app_config("lex_app")
        # The app config's own module (lex may be importable as ``lex_app`` too).
        app_module = sys.modules[type(app_config).__module__]
        with mock.patch.object(app_module, "running_in_uvicorn", return_value=True), \
                mock.patch.object(type(app_config), "is_running_in_celery", return_value=False), \
                mock.patch.object(type(app_config), "validate_initial_data_path", return_value=True), \
                mock.patch.object(LexProjectConfig, "load", return_value=config), \
                mock.patch("lex.lex_app.tests.ProcessAdminTestCase.ProcessAdminTestCase.check_if_all_models_are_empty",
                           autospec=True, side_effect=decide), \
                mock.patch("lex.lex_app.tests.ProcessAdminTestCase.ProcessAdminTestCase.get_list_of_non_empty_models",
                           return_value=[]):
            asyncio.run(app_config.async_ready({}))
        self.assertEqual(seen.get("tables"), {"default_cache", "oidc_cache"})


class TestCluster01av_CorsOrigins(SimpleTestCase):
    """D1: LEX_CORS_ALLOWED_ORIGINS."""

    # -- 1.392 ---------------------------------------------------------
    def test_1_392_cors_origins_from_the_environment(self) -> None:
        """
        Scenario 1.392: LEX_CORS_ALLOWED_ORIGINS replaces the permissive CORS defaults.
        Given: a fresh interpreter with and without LEX_CORS_ALLOWED_ORIGINS
        When: lex's settings module is imported
        Then: without it CORS allows all origins (unchanged); with it only the
              listed origins (trimmed, no trailing slash) and "*" is gone.
        """
        code = (
            "import json\nfrom lex.lex_app import settings as s\n"
            "print(json.dumps([s.CORS_ORIGIN_ALLOW_ALL, s.CORS_ALLOWED_ORIGINS, s.CORS_ORIGIN_WHITELIST]))"
        )
        allow_all, origins, _ = _fresh(code, _CHECKOUT)
        self.assertTrue(allow_all)
        self.assertIn("*", origins)
        allow_all, origins, whitelist = _fresh(
            code, _CHECKOUT, LEX_CORS_ALLOWED_ORIGINS=" https://app.example.com/, https://admin.example.com"
        )
        self.assertFalse(allow_all)
        self.assertEqual(origins, ["https://app.example.com", "https://admin.example.com"])
        self.assertEqual(whitelist, origins)
