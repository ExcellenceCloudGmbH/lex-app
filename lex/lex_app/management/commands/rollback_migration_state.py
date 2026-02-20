import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.migrations.executor import MigrationExecutor


class Command(BaseCommand):
    help = "Rollback database migrations to a previously captured migration-state snapshot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--input",
            type=str,
            default=".lex_migration_state_before.json",
            help="Input JSON path from capture_migration_state (default: .lex_migration_state_before.json).",
        )
        parser.add_argument(
            "--database",
            type=str,
            default="default",
            help="Database alias (default: default).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print rollback plan without applying it.",
        )

    def handle(self, *args, **options):
        input_path = Path(options["input"]).expanduser().resolve()
        database = options["database"]
        dry_run = options["dry_run"]

        if database not in connections.databases:
            raise CommandError(f"Unknown database alias: {database}")
        if not input_path.exists():
            raise CommandError(f"State file not found: {input_path}")

        with input_path.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        targets_payload = payload.get("targets")
        if not isinstance(targets_payload, dict):
            raise CommandError(f"Invalid rollback file format: missing dict key 'targets' in {input_path}")

        connection = connections[database]
        executor = MigrationExecutor(connection)

        targets = []
        for app_label, leafs in sorted(targets_payload.items()):
            if app_label not in executor.loader.migrated_apps:
                continue
            if not leafs:
                targets.append((app_label, None))
                continue
            for migration_name in leafs:
                targets.append((app_label, migration_name))

        if not targets:
            self.stdout.write(self.style.WARNING("No rollback targets found. Nothing to do."))
            return

        plan = executor.migration_plan(targets)
        self.stdout.write(f"Rollback migration plan contains {len(plan)} step(s).")
        if dry_run:
            for migration, backwards in plan:
                direction = "unapply" if backwards else "apply"
                self.stdout.write(f"  - {direction}: {migration.app_label}.{migration.name}")
            return

        executor.migrate(targets)
        self.stdout.write(self.style.SUCCESS("Rollback to captured migration state completed."))
