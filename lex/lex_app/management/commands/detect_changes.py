# core/management/commands/scan_model_changes.py
from django.core.management.base import BaseCommand
from django.apps import apps
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ProjectState
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.operations.models import CreateModel, DeleteModel, RenameModel

class Command(BaseCommand):
    help = "Scan pending model-level changes (add/delete/rename) before writing migrations."

    def handle(self, *args, **opts):
        # Mirror makemigrations flow but stop at the diff
        questioner = MigrationQuestioner(defaults={
            "ask_rename_model": True,  # auto-confirm model renames
        })

        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
            questioner=questioner,

        )
        changes = autodetector.changes(graph=loader.graph)

        adds, deletes, renames = [], [], []
        for app_label, migrations in changes.items():
            for mig in migrations:
                for op in mig.operations:
                    if isinstance(op, CreateModel):
                        adds.append((app_label, op.name))
                    elif isinstance(op, DeleteModel):
                        deletes.append((app_label, op.name))
                    elif isinstance(op, RenameModel):
                        renames.append((app_label, op.old_name, op.new_name))

        # Call custom logic with only names, no file parsing
        # e.g., print or invoke callbacks
        for app, name in adds:
            self.stdout.write(f"ADD {app}.{name}")
        for app, name in deletes:
            self.stdout.write(f"DELETE {app}.{name}")
        for app, old, new in renames:
            self.stdout.write(f"RENAME {app}.{old} -> {new}")
