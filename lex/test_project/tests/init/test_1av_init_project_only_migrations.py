"""`lex init` writes migrations for the project only, never into site-packages.

Intent: `lex init` ran makemigrations for every installed app. Two installed
packages ship models their own migrations don't match, so every install wrote
a migration into their folders in site-packages and applied it; the package's
next migration under the same number would conflict with it and stop
`lex init`. `oauth2_authcodeflow` sets no default_auto_field, so lex's
BigAutoField default turned its id into a bigint its migrations never made;
our api-key fork, exc-djangorestframework-api-key 1.0.0, widened id and
hashed_key to 150 characters in its model and ships no migration for it.
`lex init` now makes migrations for the project's own app. The api-key
widening ships with lex (MIGRATION_MODULES), under the very name `lex init`
generated, so databases that applied it see it as applied and new ones get
columns wide enough for the keys the fork generates (a 145-character id).
`oauth2_authcodeflow` gets the default_auto_field its migrations were made
with. A regression writes files into site-packages again, or leaves a new
instance unable to create an API key.
Cluster 1av — scenarios 1.386–1.392. Type: U / I.
Covers: lex/lex_app/management/commands/init.py (``execute_migrations``,
``project_app_label``), lex/lex_app/settings.py (INSTALLED_APPS,
MIGRATION_MODULES), lex/lex_app/vendored/.
Run: python -m lex pytest lex/test_project/tests/init/test_1av_init_project_only_migrations.py -v
"""

from __future__ import annotations

import importlib
import os
import subprocess
import sys
from io import StringIO
from pathlib import Path
from unittest import TestCase
from unittest.mock import MagicMock, patch

import pytest
from django.apps import apps
from django.test import TestCase as DjangoTestCase

pytestmark = pytest.mark.init

INIT = "lex.lex_app.management.commands.init"
REPO_ROOT = Path(__file__).resolve().parents[4]


def _command():
    from lex.lex_app.management.commands.init import Command

    cmd = Command()
    cmd.stdout, cmd.stderr = StringIO(), StringIO()
    cmd.check_unapplied_migrations = MagicMock(return_value=False)
    return cmd


def _plain(value):
    """A migration operation's deconstruction with every Field deconstructed too:
    Field instances compare by creation order, so two imports never match."""
    from django.db.models import Field

    if isinstance(value, Field):
        return ("Field", *(_plain(part) for part in value.deconstruct()[1:]))
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}
    return value


def _makemigrations_labels(mock_call_command) -> list[tuple]:
    return [c.args[1:] for c in mock_call_command.call_args_list if c.args[0] == "makemigrations"]


class TestCluster01av_InitMakesProjectMigrationsOnly(TestCase):
    """Cluster 1av: which apps `lex init` asks makemigrations about."""

    # -- 1.386 ---------------------------------------------------------
    def test_1_386_lex_init_makes_migrations_for_the_project_only(self):
        """
        Scenario 1.386: no file in site-packages
        Given: a project whose app is installed (the usual `lex init`)
        When: `lex init` runs its migration step without --makemigrations-args
        Then: makemigrations is asked about the project's app and nothing
              else, so no installed package gets a migration written into
              its folder in site-packages
        """
        cmd = _command()
        with patch(f"{INIT}.project_app_label", return_value="acme"), \
                patch(f"{INIT}.call_command") as call_command:
            cmd.execute_migrations(verbosity=0)

        self.assertEqual(_makemigrations_labels(call_command), [("acme",)])

    # -- 1.387 ---------------------------------------------------------
    def test_1_387_labels_given_on_the_command_line_win(self):
        """
        Scenario 1.387: --makemigrations-args still decides
        Given: a project
        When: `lex init --makemigrations-args "billing --empty"` runs
        Then: makemigrations gets exactly the label the caller named
        """
        cmd = _command()
        with patch(f"{INIT}.project_app_label", return_value="acme"), \
                patch(f"{INIT}.call_command") as call_command:
            cmd.execute_migrations(verbosity=0, makemigrations_extra="billing --empty")

        self.assertEqual(_makemigrations_labels(call_command), [("billing",)])

    # -- 1.388 ---------------------------------------------------------
    def test_1_388_without_a_project_app_nothing_changes(self):
        """
        Scenario 1.388: a framework checkout
        Given: lex running without a project app (the folder's name starts
               with "lex", so settings installs none)
        When: `lex init` runs its migration step
        Then: makemigrations runs for every app, as it always has
        """
        cmd = _command()
        with patch(f"{INIT}.project_app_label", return_value=None), \
                patch(f"{INIT}.call_command") as call_command:
            cmd.execute_migrations(verbosity=0)

        self.assertEqual(_makemigrations_labels(call_command), [()])

    def test_1_386_the_project_app_is_the_one_settings_installed(self):
        """Scenario 1.386 (second half): project_app_label is the project folder's
        app when settings installed it, and None otherwise (this checkout)."""
        from lex.lex_app.management.commands.init import project_app_label
        from lex.lex_app.settings import repo_name

        expected = repo_name if apps.is_installed(repo_name) else None
        self.assertEqual(project_app_label(), expected)


class TestCluster01av_NoAppDriftsFromItsMigrations(TestCase):
    """Cluster 1av: every installed app's models match the migrations it has."""

    # -- 1.389 ---------------------------------------------------------
    def test_1_389_no_installed_app_would_get_a_migration(self):
        """
        Scenario 1.389: framework and installed packages alike
        Given: the framework and every package it installs, with no project app
        When: `lex makemigrations --check --dry-run` runs for every app
        Then: nothing would be generated anywhere; 1.147 asks the same of four
              framework apps only, while `lex init` used to ask it of all
        """
        env = os.environ.copy()
        env.setdefault("PROJECT_ROOT", str(REPO_ROOT))
        result = subprocess.run(
            [sys.executable, "-m", "lex", "makemigrations", "--check", "--dry-run", "--verbosity", "1"],
            cwd=REPO_ROOT, env=env, capture_output=True, text=True, check=False,
        )

        self.assertEqual(
            result.returncode, 0,
            "An installed app's models no longer match its migrations; `lex init` on a project "
            "would leave this drift unmigrated, and a release would ship it.\n"
            f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}",
        )
        self.assertIn("No changes detected", result.stdout)

    # -- 1.391 ---------------------------------------------------------
    def test_1_391_the_vendored_api_key_migrations_start_from_the_forks_own(self):
        """
        Scenario 1.391: one history, not two
        Given: lex ships the api-key app's migrations through MIGRATION_MODULES
        When: they are compared with the ones the installed fork ships
        Then: they start with the fork's own 0001_initial, operation for
              operation, and the fork ships nothing newer; if it ever does,
              this fails so the vendored copy is reconciled or dropped
        """
        from django.conf import settings

        vendored = settings.MIGRATION_MODULES["rest_framework_api_key"]
        fork_dir = Path(importlib.import_module("rest_framework_api_key").__file__).parent / "migrations"
        fork = sorted(p.stem for p in fork_dir.glob("0*.py"))
        self.assertEqual(
            fork, ["0001_initial"],
            "exc-djangorestframework-api-key now ships migrations of its own: reconcile "
            f"{vendored} with them, and drop it once the fork ships the widening.",
        )

        def operations(module: str):
            return [_plain(op.deconstruct()) for op in importlib.import_module(module).Migration.operations]

        self.assertEqual(
            operations(f"{vendored}.0001_initial"),
            operations("rest_framework_api_key.migrations.0001_initial"),
        )

    # -- 1.392 ---------------------------------------------------------
    def test_1_392_oauth2_authcodeflow_keeps_the_id_its_migrations_made(self):
        """
        Scenario 1.392: no bigint its migrations never created
        Given: oauth2_authcodeflow, whose migrations create an AutoField id
        When: its model is loaded under lex's settings (DEFAULT_AUTO_FIELD is BigAutoField)
        Then: its app config pins default_auto_field to AutoField, so the
              model's id is the AutoField its migrations made
        """
        from django.db import models

        config = apps.get_app_config("oauth2_authcodeflow")
        self.assertEqual(config.default_auto_field, "django.db.models.AutoField")
        pk = config.get_model("BlacklistedToken")._meta.pk
        self.assertIs(type(pk), models.AutoField)


class TestCluster01av_ApiKeysFitTheirColumns(DjangoTestCase):
    """Cluster 1av: a database built by migrations can store the keys the fork makes."""

    # -- 1.390 ---------------------------------------------------------
    def test_1_390_an_api_key_can_be_created_on_a_migrated_database(self):
        """
        Scenario 1.390: wide enough for a real key
        Given: the test database, built by running every migration
        When: an API key is created the way the framework creates one
        Then: it is stored, with its 145-character id, and found again by
              its key; with the fork's own 100-character columns the insert
              fails, which is what `lex init` used to paper over
        """
        from rest_framework_api_key.models import APIKey

        api_key, key = APIKey.objects.create_key(name="cluster 1av")

        self.assertGreater(len(api_key.id), 100, "The fork's ids are longer than its migration allows.")
        self.assertEqual(APIKey.objects.get_from_key(key).pk, api_key.pk)
