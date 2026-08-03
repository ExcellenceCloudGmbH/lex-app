"""History registration is skipped in a process that only applies migrations.

Intent: starting a LEX project registers bitemporal history for every tracked
model, and that is expensive by construction -- each model gains a Level 1
``Historical<X>`` **and** a Level 2 ``Meta<Historical<X>>``, so the app registry
ends up holding three model classes per business model, each carrying the
parent's full field set. A process that only *applies* migration files never
uses any of them: ``migrate`` builds its state from the migration files, not from
the live registry. On a large project the registration alone can exhaust the
container before the first table is created (reported for a new instance whose
deploy was OOM-killed).

The saving is real but the guard has a sharp edge, and that is what these
scenarios exist to hold: ``makemigrations`` **needs** every ``Historical<X>``
present, because they are constructed at runtime. If they are absent while
autodetection runs, Django sees them as deleted models and writes migrations that
DROP the history tables -- silent, irreversible data loss. So the guard is a
positive match on known-safe command lines and nothing else; anything it cannot
positively identify must keep registering.

Cluster 1z — scenarios 1.211–1.217. Type: U.
Covers: lex/lex_app/simple_history_config.py (is_migration_only_process),
        lex/process_admin/utils/model_registration.py (register_models guard).
Run: python -m lex pytest lex/test_project/tests/init/test_1z_migration_only_history_skip.py -v
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import SimpleTestCase

from lex.lex_app.simple_history_config import is_migration_only_process
from lex.process_admin.utils.model_registration import ModelRegistration

pytestmark = pytest.mark.init


class TestCluster01z_MigrationOnlyHistorySkip(SimpleTestCase):
    """Cluster 1z: the migration-only guard, and what it must never skip."""

    def test_1_211_bare_migrate_is_migration_only(self):
        """
        Scenario 1.211: `manage.py migrate` needs no history models.
        Given: a process invoked purely to apply migration files
        When: the guard inspects the command line
        Then: it reports migration-only, so startup can skip building three
              model classes per business model for work that never reads them
        """
        self.assertTrue(
            is_migration_only_process(["manage.py", "migrate"]),
            "Applying migrations must not pay for history registration.",
        )

    def test_1_212_makemigrations_is_never_migration_only(self):
        """
        Scenario 1.212: generating migrations must always see the history models.
        Given: a command line that runs makemigrations
        When: the guard inspects it
        Then: it reports NOT migration-only. Historical models are built at
              runtime; if they are missing while autodetection runs, Django
              treats them as deleted and generates migrations that drop the
              history tables. This is the scenario that must never regress.
        """
        self.assertFalse(
            is_migration_only_process(["manage.py", "makemigrations"]),
            "Skipping history during makemigrations would generate table drops.",
        )
        self.assertFalse(
            is_migration_only_process(["manage.py", "migrate", "makemigrations"]),
            "makemigrations anywhere on the line must veto the optimisation.",
        )

    def test_1_213_generating_commands_qualify_only_when_generation_is_off(self):
        """
        Scenario 1.213: a command that *can* generate migrations needs an explicit opt-out.
        Given: `lex_migrate` / `init`, which run makemigrations unless told not to
        When: the guard inspects the line with and without --no-makemigrations
        Then: only the explicit --no-makemigrations form qualifies — the default
              form may generate, and must therefore keep the history models
        """
        self.assertFalse(
            is_migration_only_process(["manage.py", "lex_migrate"]),
            "lex_migrate generates migrations by default; it must not skip.",
        )
        self.assertTrue(
            is_migration_only_process(["manage.py", "lex_migrate", "--no-makemigrations"]),
            "With generation explicitly off, there is nothing left to protect.",
        )
    def test_1_217_init_always_registers_even_with_generation_off(self):
        """
        Scenario 1.217: `lex init` is never migration-only, whatever its flags.
        Given: the container startup line, `lex init --no-makemigrations`
        When: the guard inspects it
        Then: NOT migration-only. init applies migrations, but it also syncs
              Keycloak resources by enumerating the live app registry
              (get_all_django_models -> app_config.get_models()). Skipping
              registration there would make every Historical<X> and
              Meta<Historical<X>> invisible to that sync, silently stripping the
              history models of their Keycloak resources and permissions.
        """
        self.assertFalse(
            is_migration_only_process(["lex", "init", "--no-makemigrations", "--no-bootstrap"]),
            "init reads the registry for Keycloak sync; history must stay registered.",
        )
        self.assertFalse(
            is_migration_only_process(["manage.py", "init"]),
            "No form of init qualifies.",
        )

    def test_1_214_serving_process_is_never_migration_only(self):
        """
        Scenario 1.214: the running application always registers history.
        Given: the ASGI serve command the container actually runs
        When: the guard inspects it
        Then: NOT migration-only — the running app is precisely what needs the
              history models, and a false positive here would silently disable
              history tracking for the whole instance
        """
        self.assertFalse(
            is_migration_only_process(
                ["lex", "start", "--host", "0.0.0.0", "lex_app.asgi:application"]
            ),
            "The serving process must keep full history registration.",
        )
        self.assertFalse(
            is_migration_only_process([]), "An unrecognisable line must fall back to registering."
        )

    def test_1_215_env_override_disables_the_optimisation(self):
        """
        Scenario 1.215: the optimisation can be switched off without a release.
        Given: LEX_SKIP_MIGRATE_HISTORY=false
        When: even a plainly migration-only command line is inspected
        Then: NOT migration-only — an operator hitting an unforeseen interaction
              can restore the old behaviour on the instance rather than waiting
              for a framework fix
        """
        with patch.dict("os.environ", {"LEX_SKIP_MIGRATE_HISTORY": "false"}):
            self.assertFalse(is_migration_only_process(["manage.py", "migrate"]))
        with patch.dict("os.environ", {"LEX_SKIP_MIGRATE_HISTORY": "true"}):
            self.assertTrue(is_migration_only_process(["manage.py", "migrate"]))

    def test_1_216_register_models_skips_history_when_migration_only(self):
        """
        Scenario 1.216: the guard actually reaches registration.
        Given: a migration-only process
        When: register_models runs with history tracking requested
        Then: models are still registered, but history is not — the per-model
              work that triples the registry is what the deploy could not afford
        """
        from lex.test_project.tests.init.models import IncidentDatetimeItem

        seen = {}

        def _capture(model, untracked, history_enabled):
            seen[model.__name__] = history_enabled
            return "skipped"

        with patch.object(ModelRegistration, "_register_standard_model", side_effect=_capture), \
             patch(
                 "lex.process_admin.utils.model_registration.is_migration_only_process",
                 return_value=True,
             ):
            ModelRegistration.register_models([IncidentDatetimeItem], [], history_tracking_enabled=True)

        self.assertIn("IncidentDatetimeItem", seen, "The model must still be registered.")
        self.assertFalse(
            seen["IncidentDatetimeItem"],
            "History must be off for a migration-only process — that is the saving.",
        )

        seen.clear()
        with patch.object(ModelRegistration, "_register_standard_model", side_effect=_capture), \
             patch(
                 "lex.process_admin.utils.model_registration.is_migration_only_process",
                 return_value=False,
             ):
            ModelRegistration.register_models([IncidentDatetimeItem], [], history_tracking_enabled=True)

        self.assertTrue(
            seen["IncidentDatetimeItem"],
            "Outside a migration-only process the request must be honoured unchanged.",
        )
