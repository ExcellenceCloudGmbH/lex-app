import json
from types import SimpleNamespace

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection, transaction
from django.utils import timezone

from lex.audit_logging.models.AuditLog import AuditLog
from lex.audit_logging.models.AuditLogStatus import AuditLogStatus
from lex.audit_logging.models.CalculationLog import CalculationLog
from lex.audit_logging.serializers.AuditLogMixinSerializer import generic_instance_payload
from lex.audit_logging.utils.content_types import safe_get_content_type
from lex.audit_logging.utils.legacy_audit_payload import (
    build_legacy_calculation_payload,
    build_legacy_user_change_payload,
    merge_model_and_legacy_payload,
)


def _table_exists(table_name: str) -> bool:
    return table_name in set(connection.introspection.table_names())


def _build_model_lookup():
    lookup = {}
    for model in apps.get_models():
        aliases = {
            model._meta.model_name.lower(),
            model.__name__.lower(),
            model._meta.object_name.lower(),
        }
        for alias in aliases:
            lookup[alias] = model
    return lookup


class Command(BaseCommand):
    help = (
        "Backfill audit_logging tables from legacy archive tables "
        "(generic_app_calculationlog, generic_app_userchangelog)."
    )

    def _iter_legacy_rows(self, model_class, field_names, chunk_size):
        batch = []
        meta = SimpleNamespace(db_table=model_class._meta.db_table)
        qs = model_class.objects.order_by("timestamp").values_list(*field_names)

        for values in qs.iterator(chunk_size=chunk_size):
            row_data = dict(zip(field_names, values))
            row_data["_meta"] = meta
            batch.append(SimpleNamespace(**row_data))
            if len(batch) >= chunk_size:
                yield batch
                batch = []

        if batch:
            yield batch

    @staticmethod
    def _normalize_legacy_timestamp(value):
        if value is None:
            return timezone.now()

        current_tz = timezone.get_current_timezone()
        if settings.USE_TZ:
            if timezone.is_naive(value):
                return timezone.make_aware(value, current_tz)
            return value

        if timezone.is_aware(value):
            return timezone.make_naive(value, current_tz)
        return value

    def _insert_rows(self, cursor, table_name, columns, rows, returning_column=None):
        rows = list(rows)
        if not rows:
            return []

        quote_name = connection.ops.quote_name
        quoted_table = quote_name(table_name)
        quoted_columns = ", ".join(quote_name(column) for column in columns)
        row_placeholder = "(" + ", ".join(["%s"] * len(columns)) + ")"
        max_query_params = getattr(connection.features, "max_query_params", None)
        rows_per_statement = len(rows)

        if max_query_params:
            rows_per_statement = max(1, max_query_params // max(1, len(columns)))

        supports_bulk_returning = bool(
            returning_column
            and getattr(connection.features, "can_return_rows_from_bulk_insert", False)
        )
        supports_single_returning = bool(
            returning_column
            and getattr(connection.features, "can_return_columns_from_insert", False)
        )
        returned_ids = []

        for start in range(0, len(rows), rows_per_statement):
            batch = rows[start:start + rows_per_statement]

            if returning_column and not supports_bulk_returning:
                single_row_sql = (
                    f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES {row_placeholder}"
                )
                if supports_single_returning:
                    single_row_sql += f" RETURNING {quote_name(returning_column)}"

                for row in batch:
                    cursor.execute(single_row_sql, row)
                    if supports_single_returning:
                        returned_ids.append(cursor.fetchone()[0])
                    else:
                        inserted_id = cursor.lastrowid
                        if inserted_id is None:
                            raise RuntimeError(
                                "This database backend cannot return inserted audit log IDs."
                            )
                        returned_ids.append(inserted_id)
                continue

            values_sql = ", ".join([row_placeholder] * len(batch))
            sql = f"INSERT INTO {quoted_table} ({quoted_columns}) VALUES {values_sql}"
            if supports_bulk_returning:
                sql += f" RETURNING {quote_name(returning_column)}"

            params = []
            for row in batch:
                params.extend(row)

            cursor.execute(sql, params)
            if supports_bulk_returning:
                returned_ids.extend(inserted_id for inserted_id, in cursor.fetchall())

        return returned_ids

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
        model_snapshot_cache = {}
        payload_field = AuditLog._meta.get_field("payload")

        audit_columns = [
            AuditLog._meta.get_field("created_at").column,
            AuditLog._meta.get_field("edited_at").column,
            AuditLog._meta.get_field("date").column,
            AuditLog._meta.get_field("author").column,
            AuditLog._meta.get_field("resource").column,
            AuditLog._meta.get_field("action").column,
            AuditLog._meta.get_field("payload").column,
            AuditLog._meta.get_field("calculation_id").column,
            AuditLog._meta.get_field("content_type").column,
            AuditLog._meta.get_field("object_id").column,
        ]
        audit_status_columns = [
            AuditLogStatus._meta.get_field("audit_log").column,
            AuditLogStatus._meta.get_field("status").column,
            AuditLogStatus._meta.get_field("error_traceback").column,
            AuditLogStatus._meta.get_field("created_at").column,
            AuditLogStatus._meta.get_field("updated_at").column,
            AuditLogStatus._meta.get_field("edited_at").column,
        ]
        calculation_log_columns = [
            CalculationLog._meta.get_field("timestamp").column,
            CalculationLog._meta.get_field("calculationId").column,
            CalculationLog._meta.get_field("calculation_log").column,
            CalculationLog._meta.get_field("parent_log").column,
            CalculationLog._meta.get_field("audit_log").column,
            CalculationLog._meta.get_field("content_type").column,
            CalculationLog._meta.get_field("object_id").column,
        ]

        def resolve_content_type(model_class):
            if model_class is None:
                return None
            cache_key = model_class._meta.label_lower
            if cache_key not in content_type_cache:
                content_type_cache[cache_key] = safe_get_content_type(model_class)
            return content_type_cache[cache_key]

        def resolve_model_snapshot(resource_name: str | None, record_id):
            if not resource_name:
                return None, None, record_id, {}, None
            model_class = model_lookup.get(resource_name)
            if model_class is None:
                return None, resource_name, record_id, {}, None
            normalized_resource = model_class._meta.model_name.lower()
            if record_id is None:
                return model_class, normalized_resource, None, {}, None

            cache_key = (model_class._meta.label_lower, str(record_id))
            if cache_key in model_snapshot_cache:
                cached_record_id, cached_payload, cached_content_type_id = (
                    model_snapshot_cache[cache_key]
                )
                return (
                    model_class,
                    normalized_resource,
                    cached_record_id,
                    dict(cached_payload),
                    cached_content_type_id,
                )

            resolved_record_id = record_id
            model_payload = {"id": record_id}
            content_type_id = None
            if isinstance(resolved_record_id, int):
                content_type_id = resolve_content_type(model_class).pk
            try:
                instance = model_class._default_manager.filter(pk=record_id).first()
            except Exception:
                instance = None

            if instance is not None:
                resolved_record_id = instance.pk
                model_payload = generic_instance_payload(instance)
                if not isinstance(resolved_record_id, int):
                    content_type_id = None

            model_snapshot_cache[cache_key] = (
                resolved_record_id,
                dict(model_payload),
                content_type_id,
            )
            return (
                model_class,
                normalized_resource,
                resolved_record_id,
                dict(model_payload),
                content_type_id,
            )

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
                    with connection.cursor() as cursor:
                        for batch in self._iter_legacy_rows(
                            LegacyCalculationLog,
                            [
                                "timestamp",
                                "trigger_name",
                                "message_type",
                                "calculationId",
                                "message",
                                "method",
                                "is_notification",
                                "calculation_record",
                            ],
                            chunk_size,
                        ):
                            audit_rows = []
                            calc_rows = []
                            status_timestamps = []

                            for row in batch:
                                event_timestamp = self._normalize_legacy_timestamp(
                                    row.timestamp
                                )
                                legacy_payload, resource_name, record_id = (
                                    build_legacy_calculation_payload(
                                        row,
                                        reason,
                                        model_lookup=model_lookup,
                                    )
                                )
                                (
                                    _model_class,
                                    resource_name,
                                    record_id,
                                    model_payload,
                                    content_type_id,
                                ) = resolve_model_snapshot(resource_name, record_id)
                                payload = merge_model_and_legacy_payload(
                                    model_payload, legacy_payload
                                )
                                audit_rows.append(
                                    (
                                        event_timestamp,
                                        event_timestamp,
                                        event_timestamp,
                                        (row.trigger_name or "legacy_migration"),
                                        (resource_name or "legacy_calculation"),
                                        "update",
                                        payload_field.get_db_prep_value(
                                            payload,
                                            connection,
                                            prepared=False,
                                        ),
                                        row.calculationId,
                                        content_type_id,
                                        record_id if isinstance(record_id, int) else None,
                                    )
                                )
                                calc_rows.append(
                                    (
                                        event_timestamp,
                                        row.calculationId,
                                        f"[LEGACY:{row.message_type}] {row.message}",
                                        None,
                                        None,
                                        None,
                                        None,
                                    )
                                )
                                status_timestamps.append(event_timestamp)

                            audit_ids = self._insert_rows(
                                cursor,
                                AuditLog._meta.db_table,
                                audit_columns,
                                audit_rows,
                                returning_column=AuditLog._meta.pk.column,
                            )
                            status_rows = [
                                (
                                    audit_id,
                                    "success",
                                    None,
                                    status_timestamp,
                                    status_timestamp,
                                    status_timestamp,
                                )
                                for audit_id, status_timestamp in zip(
                                    audit_ids,
                                    status_timestamps,
                                )
                            ]
                            self._insert_rows(
                                cursor,
                                AuditLogStatus._meta.db_table,
                                audit_status_columns,
                                status_rows,
                            )

                            calculation_rows = []
                            for calc_row, audit_id in zip(calc_rows, audit_ids):
                                calculation_rows.append(
                                    (
                                        calc_row[0],
                                        calc_row[1],
                                        calc_row[2],
                                        calc_row[3],
                                        audit_id,
                                        calc_row[5],
                                        calc_row[6],
                                    )
                                )
                            self._insert_rows(
                                cursor,
                                CalculationLog._meta.db_table,
                                calculation_log_columns,
                                calculation_rows,
                            )
                            created_audit += len(audit_ids)
                            created_calc += len(calculation_rows)
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
                    with connection.cursor() as cursor:
                        for batch in self._iter_legacy_rows(
                            LegacyUserChangeLog,
                            [
                                "user_name",
                                "timestamp",
                                "message",
                                "traceback",
                                "calculationId",
                                "calculation_record",
                            ],
                            chunk_size,
                        ):
                            audit_rows = []
                            status_timestamps = []

                            for row in batch:
                                event_timestamp = self._normalize_legacy_timestamp(
                                    row.timestamp
                                )
                                legacy_payload, resource_name, record_id = (
                                    build_legacy_user_change_payload(
                                        row,
                                        reason,
                                        model_lookup=model_lookup,
                                    )
                                )
                                (
                                    _model_class,
                                    resource_name,
                                    record_id,
                                    model_payload,
                                    content_type_id,
                                ) = resolve_model_snapshot(resource_name, record_id)
                                payload = merge_model_and_legacy_payload(
                                    model_payload, legacy_payload
                                )
                                audit_rows.append(
                                    (
                                        event_timestamp,
                                        event_timestamp,
                                        event_timestamp,
                                        (row.user_name or "legacy_user"),
                                        (resource_name or "legacy_user_change"),
                                        "update",
                                        payload_field.get_db_prep_value(
                                            payload,
                                            connection,
                                            prepared=False,
                                        ),
                                        row.calculationId,
                                        content_type_id,
                                        record_id if isinstance(record_id, int) else None,
                                    )
                                )
                                status_timestamps.append(event_timestamp)

                            audit_ids = self._insert_rows(
                                cursor,
                                AuditLog._meta.db_table,
                                audit_columns,
                                audit_rows,
                                returning_column=AuditLog._meta.pk.column,
                            )
                            status_rows = [
                                (
                                    audit_id,
                                    "success",
                                    None,
                                    status_timestamp,
                                    status_timestamp,
                                    status_timestamp,
                                )
                                for audit_id, status_timestamp in zip(
                                    audit_ids,
                                    status_timestamps,
                                )
                            ]
                            self._insert_rows(
                                cursor,
                                AuditLogStatus._meta.db_table,
                                audit_status_columns,
                                status_rows,
                            )
                            created_user_audit += len(audit_ids)
            else:
                created_user_audit = user_count

            summary["created_audit_logs"] += created_user_audit

        self.stdout.write("AUDIT_BACKFILL_SUMMARY_START")
        self.stdout.write(json.dumps(summary, sort_keys=True))
        self.stdout.write("AUDIT_BACKFILL_SUMMARY_END")
