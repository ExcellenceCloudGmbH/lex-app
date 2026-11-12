"""`lex init` writes migrations for the project only, never into site-packages.

Intent: `lex init` ran makemigrations for every installed app. Two installed
packages shipped models their own migrations didn't match, so every install
wrote a migration into their folders in site-packages and applied it; the
package's next migration under the same number would conflict with it and stop
`lex init`. `oauth2_authcodeflow` set no default_auto_field, so lex's
BigAutoField default turned its id into a bigint its migrations never made;
our api-key fork widened id and hashed_key to 150 characters in its model and
shipped no migration for it. Both are fixed upstream now (django-oauth2-codeflow
3.0 pins default_auto_field; exc-djangorestframework-api-key 1.0.1 ships the
widening under the very name `lex init` generated, so databases that applied
the generated file see it as applied), and lex overrides neither. `lex init`
now makes migrations for the project's own app, so the next package that
drifts cannot write into site-packages either. A regression writes files into
site-packages again, or leaves a new instance unable to create an API key.
Cluster 1bq — scenarios 1.674–1.680. Type: U / I.
Covers: lex/lex_app/management/commands/init.py (``execute_migrations``,
``project_app_label``), lex/lex_app/settings.py (INSTALLED_APPS,
MIGRATION_MODULES).
Run: python -m lex pytest lex/test_project/tests/init/test_1bq_init_project_only_migrations.py -v
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


def _makemigrations_labels(mock_call_command) -> list[tuple]:
    return [c.args[1:] for c in mock_call_command.call_args_list if c.args[0] == "makemigrations"]


class TestCluster01bq_InitMakesProjectMigrationsOnly(TestCase):
    """Cluster 1bq: which apps `lex init` asks makemigrations about."""

    # -- 1.674 ---------------------------------------------------------
    def test_1_674_lex_init_makes_migrations_for_the_project_only(self):
        """
        Scenario 1.674: no file in site-packages
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

    # -- 1.675 ---------------------------------------------------------
    def test_1_675_labels_given_on_the_command_line_win(self):
        """
        Scenario 1.675: --makemigrations-args still decides
        Given: a project
        When: `lex init --makemigrations-args "billing --empty"` runs
        Then: makemigrations gets exactly the label the caller named
        """
        cmd = _command()
        with patch(f"{INIT}.project_app_label", return_value="acme"), \
                patch(f"{INIT}.call_command") as call_command:
            cmd.execute_migrations(verbosity=0, makemigrations_extra="billing --empty")

        self.assertEqual(_makemigrations_labels(call_command), [("billing",)])

    # -- 1.676 ---------------------------------------------------------
    def test_1_676_without_a_project_app_nothing_changes(self):
        """
        Scenario 1.676: a framework checkout
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

    def test_1_674_the_project_app_is_the_one_settings_installed(self):
        """Scenario 1.674 (second half): project_app_label is the project folder's
        app when settings installed it, and None otherwise (this checkout)."""
        from lex.lex_app.management.commands.init import project_app_label
        from lex.lex_app.settings import repo_name

        expected = repo_name if apps.is_installed(repo_name) else None
        self.assertEqual(project_app_label(), expected)


class TestCluster01bq_NoAppDriftsFromItsMigrations(TestCase):
    """Cluster 1bq: every installed app's models match the migrations it has."""

    # -- 1.677 ---------------------------------------------------------
    def test_1_677_no_installed_app_would_get_a_migration(self):
        """
        Scenario 1.677: framework and installed packages alike
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

    # -- 1.679 ---------------------------------------------------------
    def test_1_679_the_api_key_migrations_are_the_forks_own(self):
        """
        Scenario 1.679: one history, not two
        Given: exc-djangorestframework-api-key 1.0.1, which ships the migration
               widening id and hashed_key to 150 characters
        When: lex's settings and the fork's migrations are inspected
        Then: lex overrides none of the fork's migrations (no MIGRATION_MODULES
              entry), and the fork's 0002 carries the widening under the name
              `lex init` used to generate into site-packages
        """
        from django.conf import settings

        self.assertNotIn("rest_framework_api_key", settings.MIGRATION_MODULES)
        fork_dir = Path(importlib.import_module("rest_framework_api_key").__file__).parent / "migrations"
        fork = sorted(p.stem for p in fork_dir.glob("0*.py"))
        self.assertEqual(fork[:2], ["0001_initial", "0002_alter_apikey_hashed_key_alter_apikey_id"])

        widened = {
            op.name: op.field.max_length
            for op in importlib.import_module(
                "rest_framework_api_key.migrations.0002_alter_apikey_hashed_key_alter_apikey_id"
            ).Migration.operations
        }
        self.assertEqual(widened, {"hashed_key": 150, "id": 150})

    # -- 1.680 ---------------------------------------------------------
    def test_1_680_oauth2_authcodeflow_keeps_the_id_its_migrations_made(self):
        """
        Scenario 1.680: no bigint its migrations never created
        Given: oauth2_authcodeflow, whose migrations create an AutoField id
        When: its model is loaded under lex's settings (DEFAULT_AUTO_FIELD is BigAutoField)
        Then: its app config (django-oauth2-codeflow 3.0's own) pins
              default_auto_field to AutoField, so the model's id is the
              AutoField its migrations made
        """
        from django.db import models

        config = apps.get_app_config("oauth2_authcodeflow")
        self.assertEqual(config.default_auto_field, "django.db.models.AutoField")
        pk = config.get_model("BlacklistedToken")._meta.pk
        self.assertIs(type(pk), models.AutoField)


class TestCluster01bq_ApiKeysFitTheirColumns(DjangoTestCase):
    """Cluster 1bq: a database built by migrations can store the keys the fork makes."""

    # -- 1.678 ---------------------------------------------------------
    def test_1_678_an_api_key_can_be_created_on_a_migrated_database(self):
        """
        Scenario 1.678: wide enough for a real key
        Given: the test database, built by running every migration
        When: an API key is created the way the framework creates one
        Then: it is stored, with its 145-character id, and found again by
              its key; with the fork's own 100-character columns the insert
              fails, which is what `lex init` used to paper over
        """
        from rest_framework_api_key.models import APIKey

        api_key, key = APIKey.objects.create_key(name="cluster 1bq")

        self.assertGreater(len(api_key.id), 100, "The fork's ids are longer than its migration allows.")
        self.assertEqual(APIKey.objects.get_from_key(key).pk, api_key.pk)
