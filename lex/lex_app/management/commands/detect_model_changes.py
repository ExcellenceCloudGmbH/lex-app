"""
Detect Django model changes and check for models missing from Keycloak.

Usage:
    python manage.py detect_model_changes
    python manage.py detect_model_changes --output /tmp/changes.json
    python manage.py detect_model_changes --no-check-missing

Outputs a JSON file (default: .model_changes.json) with the structure:
    {
        "adds":           [["app_label", "ModelName"], ...],
        "deletes":        [["app_label", "ModelName"], ...],
        "renames":        [["app_label", "OldName", "NewName"], ...],
        "missing_models": [["app_label", "ModelName"], ...]
    }

This file is consumed by the ``sync_keycloak`` command.
"""
import json
import logging
import os
from pathlib import Path

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.models import CreateModel, DeleteModel, RenameModel
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ProjectState

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT = Path(os.getcwd()) / ".model_changes.json"


class Command(BaseCommand):
    help = (
        "Detect Django model changes (adds / deletes / renames) via the migration "
        "autodetector and optionally check for models missing from Keycloak. "
        "Writes results as JSON for the sync_keycloak command."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=str(DEFAULT_OUTPUT),
            help=f"Path for the JSON output file (default: {DEFAULT_OUTPUT}).",
        )
        parser.add_argument(
            "--no-check-missing",
            action="store_true",
            default=False,
            help="Skip the Keycloak missing-models check (only detect migration changes).",
        )

    # ── Helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _detect_migration_changes():
        """
        Use Django's MigrationAutodetector to find pending model operations
        (CreateModel, DeleteModel, RenameModel) *before* migrations are applied.
        """
        questioner = MigrationQuestioner(defaults={"ask_rename_model": True})
        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
            questioner=questioner,
        )
        changes = autodetector.changes(graph=loader.graph)

        adds, deletes, renames = [], [], []

        for app_label, migrations in changes.items():
            for migration in migrations:
                for operation in migration.operations:
                    if isinstance(operation, CreateModel):
                        adds.append((app_label, operation.name))
                    elif isinstance(operation, DeleteModel):
                        deletes.append((app_label, operation.name))
                    elif isinstance(operation, RenameModel):
                        renames.append((app_label, operation.old_name, operation.new_name))

        return adds, deletes, renames

    @staticmethod
    def _check_missing_models(renames):
        """
        Compare all Django models against existing Keycloak resources and
        return a set of model identifiers (``app_label.ModelName``) that are
        present in Django but absent from Keycloak.
        """
        from lex_app.management.commands.Init import KeycloakSyncManager

        sync_manager = KeycloakSyncManager()
        auth_config = sync_manager.export_configs()

        if not auth_config:
            logger.warning("Could not export Keycloak authorization config; skipping missing-models check.")
            return set()

        all_django_models = sync_manager.get_all_django_models()
        existing_keycloak_resources = sync_manager.get_existing_keycloak_resources(auth_config)
        missing_models = sync_manager.find_missing_models(
            all_django_models, existing_keycloak_resources, set()
        )

        # Exclude models that are the *target* of a rename — they will be
        # handled by the rename operation itself, not as a new add.
        for app_name, _old_name, new_name in renames:
            key = f"{app_name}.{new_name}"
            if key in missing_models:
                missing_models.remove(key)

        return missing_models

    # ── Main ──────────────────────────────────────────────────────────────

    def handle(self, *args, **options):
        output_path = Path(options["output"])
        check_missing = not options["no_check_missing"]

        # ── 1. Detect migration-level model changes ──────────────────────
        self.stdout.write("=" * 80)
        self.stdout.write("Detecting Model Changes")
        self.stdout.write("=" * 80)

        adds, deletes, renames = self._detect_migration_changes()

        if adds or deletes or renames:
            self.stdout.write("Detected model changes:")
            for app, name in adds:
                self.stdout.write(f"  ADD    {app}.{name}")
            for app, name in deletes:
                self.stdout.write(f"  DELETE {app}.{name}")
            for app, old, new in renames:
                self.stdout.write(f"  RENAME {app}.{old} -> {new}")
        else:
            self.stdout.write("No model changes detected.")

        # ── 2. Optionally check for models missing from Keycloak ─────────
        missing_models_list = []

        if check_missing:
            self.stdout.write("\nChecking for Django models missing from Keycloak...")
            missing_models = self._check_missing_models(renames)

            if missing_models:
                self.stdout.write(f"Found {len(missing_models)} missing models:")
                for model_name in sorted(missing_models):
                    self.stdout.write(f"  MISSING: {model_name}")
                missing_models_list = [tuple(m.split(".")) for m in missing_models]
            else:
                self.stdout.write("No missing models found.")
        else:
            self.stdout.write("\nSkipping Keycloak missing-models check (--no-check-missing).")

        # ── 3. Write JSON output ─────────────────────────────────────────
        result = {
            "adds": [list(a) for a in adds],
            "deletes": [list(d) for d in deletes],
            "renames": [list(r) for r in renames],
            "missing_models": [list(m) for m in missing_models_list],
        }

        output_path.write_text(json.dumps(result, indent=2), encoding="utf-8")
        self.stdout.write(
            self.style.SUCCESS(f"\n✓ Model changes written to {output_path}")
        )
