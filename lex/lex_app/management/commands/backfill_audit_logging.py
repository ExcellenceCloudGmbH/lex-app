import json

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db import connection, transaction

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.serializers.AuditLogMixinSerializer import generic_instance_payload
from lex.audit_logging.utils.legacy_audit_payload import (
    build_legacy_calculation_payload,
    build_legacy_user_change_payload,
    merge_model_and_legacy_payload,
)


def _table_exists(table_name: str) -> bool:
    return table_name in set(connection.introspection.table_names())


def _build_model_lookup():
    return {model._meta.model_name.lower(): model for model in apps.get_models()}


class Command(BaseCommand):
    help = (
        "Backfill audit_logging tables from legacy archive tables "
        "(generic_app_calculationlog, generic_app_userchangelog)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--reason",
            type=str,
            default="V1 audit log migration snapshot",
            help="Reason string attached to payload metadata.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Iterator chunk size (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report actions without writing rows.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Run even if audit_logging tables already contain rows.",
        )

    def handle(self, *args, **options):
        reason = options["reason"]
        chunk_size = max(1, int(options["chunk_size"]))
        dry_run = options["dry_run"]
        force = options["force"]
        model_lookup = _build_model_lookup()
        content_type_cache = {}

        def resolve_content_type(resource_name: str | None):
            if not resource_name:
                return None
            if resource_name not in content_type_cache:
                model_class = model_lookup.get(resource_name)
                content_type_cache[resource_name] = (
                    ContentType.objects.get_for_model(model_class)
                    if model_class is not None
                    else None
                )
            return content_type_cache[resource_name]

        def resolve_model_snapshot(resource_name: str | None, record_id):
            if not resource_name or record_id is None:
                return None, None
            model_class = model_lookup.get(resource_name)
            if model_class is None:
                return None, None
            try:
                instance = model_class._default_manager.filter(pk=record_id).first()
            except Exception:
                return model_class, None
            return model_class, instance

        existing_audit = AuditLog.objects.count()
        existing_calc = CalculationLog.objects.count()
        if (existing_audit > 0 or existing_calc > 0) and not force:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipping audit backfill: audit_logging already populated "
                    f"(audit={existing_audit}, calc={existing_calc}). Use --force to override."
                )
            )
            self.stdout.write("AUDIT_BACKFILL_SUMMARY_START")
            self.stdout.write(
                json.dumps(
                    {
                        "dry_run": dry_run,
                        "skipped": True,
                        "reason": "audit_logging_not_empty",
                        "existing_audit_rows": existing_audit,
                        "existing_calculation_log_rows": existing_calc,
                    },
                    sort_keys=True,
                )
            )
            self.stdout.write("AUDIT_BACKFILL_SUMMARY_END")
            return

        summary = {
            "dry_run": dry_run,
            "reason": reason,
            "legacy_rows_read": 0,
            "created_audit_logs": 0,
            "created_calculation_logs": 0,
            "sources": [],
        }

        LegacyCalculationLog = apps.get_model("legacy_data", "LegacyCalculationLog")
        LegacyUserChangeLog = apps.get_model("legacy_data", "LegacyUserChangeLog")

        # Source 1: generic_app_calculationlog -> audit_logging.AuditLog + CalculationLog
        if _table_exists(LegacyCalculationLog._meta.db_table):
            calc_count = LegacyCalculationLog.objects.count()
            summary["legacy_rows_read"] += calc_count
            summary["sources"].append(
                {"table": LegacyCalculationLog._meta.db_table, "rows": calc_count}
            )
            self.stdout.write(f"Processing {calc_count} rows from {LegacyCalculationLog._meta.db_table}")

            created_audit = 0
            created_calc = 0
            if not dry_run:
                with transaction.atomic():
                    qs = LegacyCalculationLog.objects.all().order_by("timestamp")
                    for row in qs.iterator(chunk_size=chunk_size):
                        legacy_payload, resource_name, record_id = build_legacy_calculation_payload(row, reason)
                        model_class, instance = resolve_model_snapshot(
                            resource_name, record_id
                        )
                        model_payload = (
                            generic_instance_payload(instance)
                            if instance is not None
                            else ({"id": record_id} if record_id is not None else {})
                        )
                        payload = merge_model_and_legacy_payload(
                            model_payload, legacy_payload
                        )
                        content_type = (
                            resolve_content_type(resource_name)
                            if model_class is not None and isinstance(record_id, int)
                            else None
                        )
                        audit_log = AuditLog.objects.create(
                            author=(row.trigger_name or "legacy_migration"),
                            resource=(resource_name or "legacy_calculation"),
                            action="update",
                            payload=payload,
                            calculation_id=row.calculationId,
                            content_type=content_type,
                            object_id=record_id if isinstance(record_id, int) else None,
                        )
                        AuditLogStatus.objects.create(audit_log=audit_log, status="success")
                        CalculationLog.objects.create(
                            calculationId=row.calculationId,
                            calculation_log=f"[LEGACY:{row.message_type}] {row.message}",
                            audit_log=audit_log,
                        )
                        created_audit += 1
                        created_calc += 1
            else:
                created_audit = calc_count
                created_calc = calc_count

            summary["created_audit_logs"] += created_audit
            summary["created_calculation_logs"] += created_calc

        # Source 2: generic_app_userchangelog -> audit_logging.AuditLog
        if _table_exists(LegacyUserChangeLog._meta.db_table):
            user_count = LegacyUserChangeLog.objects.count()
            summary["legacy_rows_read"] += user_count
            summary["sources"].append(
                {"table": LegacyUserChangeLog._meta.db_table, "rows": user_count}
            )
            self.stdout.write(f"Processing {user_count} rows from {LegacyUserChangeLog._meta.db_table}")

            created_user_audit = 0
            if not dry_run:
                with transaction.atomic():
                    qs = LegacyUserChangeLog.objects.all().order_by("timestamp")
                    for row in qs.iterator(chunk_size=chunk_size):
                        legacy_payload, resource_name, record_id = build_legacy_user_change_payload(row, reason)
                        model_class, instance = resolve_model_snapshot(
                            resource_name, record_id
                        )
                        model_payload = (
                            generic_instance_payload(instance)
                            if instance is not None
                            else ({"id": record_id} if record_id is not None else {})
                        )
                        payload = merge_model_and_legacy_payload(
                            model_payload, legacy_payload
                        )
                        content_type = (
                            resolve_content_type(resource_name)
                            if model_class is not None and isinstance(record_id, int)
                            else None
                        )
                        audit_log = AuditLog.objects.create(
                            author=(row.user_name or "legacy_user"),
                            resource=(resource_name or "legacy_user_change"),
                            action="update",
                            payload=payload,
                            calculation_id=row.calculationId,
                            content_type=content_type,
                            object_id=record_id if isinstance(record_id, int) else None,
                        )
                        AuditLogStatus.objects.create(audit_log=audit_log, status="success")
                        created_user_audit += 1
            else:
                created_user_audit = user_count

            summary["created_audit_logs"] += created_user_audit

        self.stdout.write("AUDIT_BACKFILL_SUMMARY_START")
        self.stdout.write(json.dumps(summary, sort_keys=True))
        self.stdout.write("AUDIT_BACKFILL_SUMMARY_END")
