import json
from pathlib import Path
from typing import Dict, List, Optional, Set

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.utils import timezone

EXPLICIT_SYSTEM_TABLES: Set[str] = {
    "django_migrations",
    "django_content_type",
    "django_session",
    "auth_user",
    "auth_group",
    "auth_permission",
    "auth_group_permissions",
    "auth_user_groups",
    "auth_user_user_permissions",
    "django_admin_log",
}

SYSTEM_PREFIXES = (
    "django_",
    "auth_",
    "admin_",
    "sessions_",
    "contenttypes_",
    "oauth2_",
    "oauth_",
    "authtoken_",
    "django_celery_beat_",
    "celery_",
    "simple_history_",
)


def _is_system_table(table_name: str) -> bool:
    if table_name in EXPLICIT_SYSTEM_TABLES:
        return True
    return table_name.startswith(SYSTEM_PREFIXES)


def _get_model_tables() -> Set[str]:
    tables: Set[str] = set()
    for model in apps.get_models():
        if getattr(model._meta, "db_table", None):
            tables.add(model._meta.db_table)
    return tables


def _get_primary_key_column(connection, table_name: str) -> Optional[str]:
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, table_name)
    for constraint in constraints.values():
        if constraint.get("primary_key"):
            columns = constraint.get("columns") or []
            if columns:
                return columns[0]
    return None


class Command(BaseCommand):
    help = (
        "Generate a legacy-freeze manifest by diffing a pre-migration table snapshot "
        "against current V2 model tables and known system tables."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--before",
            type=str,
            required=True,
            help="Path to JSON snapshot produced by `lex capture_db_tables`.",
        )
        parser.add_argument(
            "--output",
            type=str,
            default=".lex_legacy_freeze_manifest.json",
            help="Output manifest path (default: .lex_legacy_freeze_manifest.json).",
        )
        parser.add_argument(
            "--database",
            type=str,
            default="default",
            help="Database alias from Django settings (default: default).",
        )

    def handle(self, *args, **options):
        before_path = Path(options["before"]).expanduser().resolve()
        output_path = Path(options["output"]).expanduser().resolve()
        database = options["database"]

        if database not in connections.databases:
            raise CommandError(f"Unknown database alias: {database}")
        if not before_path.exists():
            raise CommandError(f"Snapshot file not found: {before_path}")

        with before_path.open("r", encoding="utf-8") as handle:
            before_data = json.load(handle)

        before_tables_raw = before_data.get("tables")
        if not isinstance(before_tables_raw, list):
            raise CommandError(
                f"Snapshot format invalid: expected key 'tables' as list in {before_path}"
            )

        before_tables = {str(t) for t in before_tables_raw}
        connection = connections[database]
        current_tables = set(connection.introspection.table_names())
        model_tables = _get_model_tables()

        existing_from_before = before_tables.intersection(current_tables)
        excluded_model = existing_from_before.intersection(model_tables)
        excluded_system = {t for t in existing_from_before if _is_system_table(t)}

        candidates = existing_from_before - excluded_model - excluded_system

        freeze_tables: List[str] = []
        skipped_no_pk: Dict[str, str] = {}
        for table_name in sorted(candidates):
            pk_column = _get_primary_key_column(connection, table_name)
            if not pk_column:
                skipped_no_pk[table_name] = "no_primary_key_detected"
                continue
            freeze_tables.append(table_name)

        payload = {
            "generated_at": timezone.now().isoformat(),
            "database": database,
            "snapshot_file": str(before_path),
            "before_table_count": len(before_tables),
            "current_table_count": len(current_tables),
            "model_table_count": len(model_tables),
            "freeze_table_count": len(freeze_tables),
            "freeze_tables": freeze_tables,
            "skipped_no_pk_tables": skipped_no_pk,
            "excluded_system_tables": sorted(excluded_system),
            "excluded_v2_model_tables": sorted(excluded_model),
        }

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with output_path.open("w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)

        self.stdout.write(
            self.style.SUCCESS(
                f"Generated legacy freeze manifest with {len(freeze_tables)} tables at {output_path}"
            )
        )
        if skipped_no_pk:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipped {len(skipped_no_pk)} table(s) without primary key."
                )
            )
