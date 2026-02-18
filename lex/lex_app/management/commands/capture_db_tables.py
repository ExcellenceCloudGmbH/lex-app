import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone


class Command(BaseCommand):
    help = "Capture a snapshot of physical DB table names into a JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--output",
            type=str,
            default=".lex_tables_before.json",
            help="Output JSON file path (default: .lex_tables_before.json).",
        )
        parser.add_argument(
            "--database",
            type=str,
            default="default",
            help="Database alias from Django settings (default: default).",
        )

    def handle(self, *args, **options):
        output_path = Path(options["output"]).expanduser().resolve()
        database = options["database"]

        if database not in connections.databases:
            raise CommandError(f"Unknown database alias: {database}")

        connection = connections[database]
        tables = sorted(connection.introspection.table_names())

        payload = {
            "captured_at": timezone.now().isoformat(),
            "database": database,
            "tables": tables,
            "table_count": len(tables),
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Captured {len(tables)} tables to {output_path}"
            )
        )
