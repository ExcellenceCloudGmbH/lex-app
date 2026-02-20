import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder
from django.utils import timezone


class Command(BaseCommand):
    help = "Capture current migration targets per app to a JSON rollback-state file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=".lex_migration_state_before.json",
            help="Output JSON path (default: .lex_migration_state_before.json).",
        )
        parser.add_argument(
            "--database",
            type=str,
            default="default",
            help="Database alias (default: default).",
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"]).expanduser().resolve()
        database = options["database"]

        if database not in connections.databases:
            raise CommandError(f"Unknown database alias: {database}")

        connection = connections[database]
        recorder = MigrationRecorder(connection)

        capture_mode = "loader_leafs"
        capture_warning = None
        targets = {}
        raw_applied = sorted(recorder.applied_migrations())

        try:
            loader = MigrationLoader(connection, ignore_no_migrations=True)
            applied = set(raw_applied)
            for app_label in sorted(loader.migrated_apps):
                leaf_nodes = loader.graph.leaf_nodes(app_label)
                applied_leafs = sorted(
                    [
                        migration_name
                        for _, migration_name in leaf_nodes
                        if (_, migration_name) in applied
                    ]
                )
                targets[app_label] = applied_leafs
        except Exception as exc:
            # Fallback for broken/legacy migration modules on disk.
            # This keeps workflow progress by capturing recorder state only.
            capture_mode = "recorder_fallback"
            capture_warning = (
                "MigrationLoader failed while importing migration modules. "
                "Falling back to recorder-based capture."
            )
            latest_by_app = {}
            ordered_rows = (
                recorder.migration_qs.order_by("applied", "app", "name").values_list(
                    "app", "name"
                )
            )
            for app_label, migration_name in ordered_rows:
                latest_by_app[app_label] = migration_name

            for app_label in sorted(latest_by_app.keys()):
                targets[app_label] = [latest_by_app[app_label]]

            self.stdout.write(
                self.style.WARNING(f"{capture_warning} Loader error: {exc}")
            )

        payload = {
            "captured_at": timezone.now().isoformat(),
            "database": database,
            "capture_mode": capture_mode,
            "capture_warning": capture_warning,
            "targets": targets,
            "raw_applied_migrations": [
                {"app": app_label, "name": migration_name}
                for app_label, migration_name in raw_applied
            ],
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Captured migration state for {len(targets)} apps to {output_path}"
            )
        )
