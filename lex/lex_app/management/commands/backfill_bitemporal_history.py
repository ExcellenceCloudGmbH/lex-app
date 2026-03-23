import json
from dataclasses import dataclass
from typing import Dict, Iterable, List, Tuple

from django.apps import apps
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lex.core.services.StandardHistory import create_standard_history_record
from lex.core.services.bitemporal_signals import (
    suppress_main_table_sync,
    suppress_meta_sys_to_chaining,
)

EXCLUDED_APP_LABELS = {
    "legacy_data",
    "auth",
    "contenttypes",
    "sessions",
    "admin",
    "oauth2_authcodeflow",
    "simple_history",
}


def _iter_tracked_models() -> Iterable[Tuple[type, type, type]]:
    for model in apps.get_models():
        if model._meta.abstract or model._meta.proxy:
            continue
        if model.__name__.startswith("Historical"):
            continue
        if model._meta.app_label in EXCLUDED_APP_LABELS:
            continue

        history_manager = getattr(model, "history", None)
        if history_manager is None:
            continue

        history_model = getattr(history_manager, "model", None)
        if history_model is None:
            continue
        if not hasattr(history_model, "meta_history"):
            continue

        meta_model = history_model.meta_history.model
        if meta_model is None:
            continue

        yield model, history_model, meta_model


def _missing_history_queryset(model, history_model, pk_lookup: str):
    history_exists = history_model._default_manager.filter(
        **{pk_lookup: OuterRef(pk_lookup)}
    )
    return model._default_manager.annotate(
        _has_history=Exists(history_exists)
    ).filter(_has_history=False)


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


@dataclass(frozen=True)
class _CopyColumn:
    attname: str
    source_column: str
    target_column: str


class Command(BaseCommand):
    help = (
        "Backfill bitemporal history (History + MetaHistory) through SQL bulk operations "
        "using a single migration timestamp."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--timestamp",
            type=str,
            default=None,
            help="ISO8601 timestamp to use for all valid_from/sys_from values.",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Iterator chunk size for fallback ORM path (default: 500).",
        )
        parser.add_argument(
            "--reason",
            type=str,
            default="V1 migration snapshot",
            help="history_change_reason value for backfilled rows.",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Compute and report actions without writing history rows.",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._table_description_cache: Dict[str, Dict[str, object]] = {}
        self._table_columns_cache: Dict[str, set[str]] = {}
        self._runtime_schema_warnings: set[tuple[str, str, str]] = set()

    def _resolve_timestamp(self, raw_timestamp: str | None):
        if raw_timestamp is None:
            return timezone.now()

        parsed = parse_datetime(raw_timestamp)
        if parsed is None:
            raise CommandError(
                f"Invalid --timestamp value: {raw_timestamp}. Expected ISO8601."
            )
        current_tz = timezone.get_current_timezone()
        if settings.USE_TZ:
            if timezone.is_naive(parsed):
                parsed = timezone.make_aware(parsed, current_tz)
        else:
            if timezone.is_aware(parsed):
                parsed = timezone.make_naive(parsed, current_tz)
        return parsed

    def _is_future_backfill(self, timestamp) -> bool:
        now = timezone.now()
        current_tz = timezone.get_current_timezone()

        if settings.USE_TZ:
            if timezone.is_naive(timestamp):
                timestamp = timezone.make_aware(timestamp, current_tz)
            if timezone.is_naive(now):
                now = timezone.make_aware(now, current_tz)
        else:
            if timezone.is_aware(timestamp):
                timestamp = timezone.make_naive(timestamp, current_tz)
            if timezone.is_aware(now):
                now = timezone.make_naive(now, current_tz)

        return timestamp > now + timezone.timedelta(seconds=5)

    def _q(self, identifier: str) -> str:
        return connection.ops.quote_name(identifier)

    def _table_description_map(self, table_name: str) -> Dict[str, object]:
        cached = self._table_description_cache.get(table_name)
        if cached is not None:
            return cached

        with connection.cursor() as cursor:
            description = {
                column.name: column
                for column in connection.introspection.get_table_description(
                    cursor, table_name
                )
            }
        self._table_description_cache[table_name] = description
        return description

    def _table_attnames(self, table_name: str) -> set[str]:
        cached = self._table_columns_cache.get(table_name)
        if cached is not None:
            return cached

        columns = set(self._table_description_map(table_name))
        self._table_columns_cache[table_name] = columns
        return columns

    def _validate_required_table_columns(
        self,
        *,
        label: str,
        table_name: str,
        required_columns: Iterable[str],
    ) -> None:
        actual = self._table_attnames(table_name)
        missing = sorted(set(required_columns) - actual)
        if missing:
            raise CommandError(
                f"Unsupported runtime schema for {label}: "
                f'table "{table_name}" is missing required columns {missing}.'
            )

    def _field_by_attname(self, model_class, attname: str):
        for field in model_class._meta.concrete_fields:
            if field.attname == attname:
                return field
        raise CommandError(
            f"Unable to resolve field {attname!r} on {model_class._meta.label}."
        )

    def _field_column_by_attname(self, model_class, attname: str) -> str:
        return self._field_by_attname(model_class, attname).column

    def _validate_omitted_copy_columns(
        self,
        *,
        label: str,
        source_table: str,
        target_table: str,
        target_model,
        missing_source: Iterable[str],
    ) -> None:
        required_missing: List[str] = []
        target_description = self._table_description_map(target_table)

        for attname in missing_source:
            field = self._field_by_attname(target_model, attname)
            if field.column not in target_description:
                continue

            if field.primary_key or field.auto_created:
                continue
            if field.has_default():
                continue

            column = target_description[field.column]
            if getattr(column, "null_ok", False):
                continue
            if getattr(column, "default", None) is not None:
                continue

            required_missing.append(attname)

        if required_missing:
            raise CommandError(
                f"Unsupported runtime schema for {label}: cannot backfill into "
                f'"{target_table}" because required columns {sorted(required_missing)} '
                f'are missing from source table "{source_table}" and target table '
                f'"{target_table}" cannot supply defaults for them.'
            )

    def _warn_runtime_schema_drift(
        self,
        *,
        label: str,
        source_table: str,
        target_table: str,
        missing_source: List[str],
        missing_target: List[str],
    ) -> None:
        if not missing_source and not missing_target:
            return

        warning_key = (
            label,
            ",".join(missing_source),
            ",".join(missing_target),
        )
        if warning_key in self._runtime_schema_warnings:
            return
        self._runtime_schema_warnings.add(warning_key)

        parts: List[str] = []
        if missing_source:
            parts.append(f'{source_table} missing {missing_source}')
        if missing_target:
            parts.append(f'{target_table} missing {missing_target}')
        self.stdout.write(
            self.style.WARNING(
                f"{label}: omitting schema-drifted columns during backfill "
                f"({'; '.join(parts)})"
            )
        )

    def _runtime_copy_columns(
        self,
        *,
        label: str,
        source_table: str,
        target_table: str,
        source_model,
        target_model,
        attnames: List[str],
    ) -> List[_CopyColumn]:
        source_columns = self._table_attnames(source_table)
        target_columns = self._table_attnames(target_table)

        missing_source: List[str] = []
        missing_target: List[str] = []
        copy_columns: List[_CopyColumn] = []

        for attname in attnames:
            source_field = self._field_by_attname(source_model, attname)
            target_field = self._field_by_attname(target_model, attname)
            source_missing = source_field.column not in source_columns
            target_missing = target_field.column not in target_columns
            if source_missing:
                missing_source.append(attname)
            if target_missing:
                missing_target.append(attname)
            if source_missing or target_missing:
                continue
            copy_columns.append(
                _CopyColumn(
                    attname=attname,
                    source_column=source_field.column,
                    target_column=target_field.column,
                )
            )

        missing_source = sorted(_dedupe(missing_source))
        missing_target = sorted(_dedupe(missing_target))
        omitted_attnames = _dedupe([*missing_source, *missing_target])

        self._validate_omitted_copy_columns(
            label=label,
            source_table=source_table,
            target_table=target_table,
            target_model=target_model,
            missing_source=missing_source,
        )
        self._warn_runtime_schema_drift(
            label=label,
            source_table=source_table,
            target_table=target_table,
            missing_source=missing_source,
            missing_target=missing_target,
        )
        return copy_columns

    def _history_backfill_requires_orm(self, model) -> bool:
        return any(not parent._meta.abstract for parent in model._meta.parents)

    def _orm_backfill_supported(
        self,
        *,
        model,
        history_model,
        meta_model,
    ) -> bool:
        for field in model._meta.concrete_fields:
            if field.column not in self._table_attnames(field.model._meta.db_table):
                return False

        expected_history = {
            field.column for field in history_model._meta.concrete_fields
        }
        if not expected_history.issubset(
            self._table_attnames(history_model._meta.db_table)
        ):
            return False

        expected_meta = {field.column for field in meta_model._meta.concrete_fields}
        return expected_meta.issubset(self._table_attnames(meta_model._meta.db_table))

    def _history_copy_columns(self, model, history_model) -> List[_CopyColumn]:
        main_attnames = {field.attname for field in model._meta.concrete_fields}

        attnames = [
            field.attname
            for field in getattr(history_model, "tracked_fields", ())
            if field.attname in main_attnames
        ]
        if not attnames:
            history_attnames = {
                field.attname for field in history_model._meta.concrete_fields
            }
            attnames = [
                field.attname
                for field in model._meta.concrete_fields
                if field.attname in history_attnames
            ]

        attnames = _dedupe(attnames)
        if not attnames:
            raise CommandError(
                f"Unable to infer history copy columns for {model._meta.label}."
            )
        return self._runtime_copy_columns(
            label=model._meta.label,
            source_table=model._meta.db_table,
            target_table=history_model._meta.db_table,
            source_model=model,
            target_model=history_model,
            attnames=attnames,
        )

    def _meta_copy_columns(self, history_model, meta_model) -> List[_CopyColumn]:
        history_attnames = {field.attname for field in history_model._meta.concrete_fields}

        attnames = [
            field.attname
            for field in getattr(meta_model, "tracked_fields", ())
            if field.attname in history_attnames
        ]
        if not attnames:
            meta_attnames = {field.attname for field in meta_model._meta.concrete_fields}
            attnames = [
                field.attname
                for field in history_model._meta.concrete_fields
                if field.attname in meta_attnames
            ]

        attnames = _dedupe(attnames)
        return self._runtime_copy_columns(
            label=history_model._meta.label,
            source_table=history_model._meta.db_table,
            target_table=meta_model._meta.db_table,
            source_model=history_model,
            target_model=meta_model,
            attnames=attnames,
        )

    def _append_required_defaults(
        self,
        *,
        model_class,
        table_name: str,
        insert_cols: List[str],
        select_exprs: List[str],
        params: List[object],
    ) -> None:
        """
        Fill non-null fields that are omitted from SQL inserts with model defaults.

        Django model defaults are not always reflected as DB-level defaults, so
        raw INSERT statements must provide these values explicitly.
        """
        inserted = set(insert_cols)
        actual_columns = self._table_attnames(table_name)
        for field in model_class._meta.concrete_fields:
            if field.column in inserted:
                continue
            if field.primary_key or field.auto_created:
                continue
            if field.column not in actual_columns:
                continue
            if field.null:
                continue
            if not field.has_default():
                continue

            insert_cols.append(field.column)
            select_exprs.append("%s")
            params.append(field.get_default())
            inserted.add(field.column)

    def _validate_sql_schema(
        self,
        *,
        model,
        history_model,
        meta_model,
    ) -> None:
        history_cols = {field.column for field in history_model._meta.concrete_fields}
        meta_cols = {field.column for field in meta_model._meta.concrete_fields}
        pk_column = model._meta.pk.column

        required_history = {
            pk_column,
            history_model._meta.pk.column,
            history_model._meta.get_field("valid_from").column,
            history_model._meta.get_field("history_type").column,
            history_model._meta.get_field("history_change_reason").column,
        }
        required_meta = {
            meta_model._meta.pk.column,
            meta_model._meta.get_field("sys_from").column,
            meta_model._meta.get_field("meta_history_type").column,
            meta_model._meta.get_field("meta_history_change_reason").column,
            self._field_column_by_attname(meta_model, "history_object_id"),
        }

        missing_history = sorted(required_history - history_cols)
        missing_meta = sorted(required_meta - meta_cols)
        if missing_history or missing_meta:
            raise CommandError(
                "Unsupported bitemporal schema for "
                f"{model._meta.label}: "
                f"missing history columns={missing_history}, "
                f"missing meta columns={missing_meta}"
            )

        self._validate_required_table_columns(
            label=model._meta.label,
            table_name=model._meta.db_table,
            required_columns={pk_column},
        )
        self._validate_required_table_columns(
            label=history_model._meta.label,
            table_name=history_model._meta.db_table,
            required_columns={
                pk_column,
                history_model._meta.pk.column,
                history_model._meta.get_field("valid_from").column,
                history_model._meta.get_field("valid_to").column,
                history_model._meta.get_field("history_type").column,
                history_model._meta.get_field("history_change_reason").column,
            },
        )
        self._validate_required_table_columns(
            label=meta_model._meta.label,
            table_name=meta_model._meta.db_table,
            required_columns={
                meta_model._meta.pk.column,
                meta_model._meta.get_field("sys_from").column,
                meta_model._meta.get_field("sys_to").column,
                meta_model._meta.get_field("meta_history_type").column,
                meta_model._meta.get_field("meta_history_change_reason").column,
                self._field_column_by_attname(meta_model, "history_object_id"),
            },
        )

    def _count_missing_history_rows_sql(
        self,
        cursor,
        *,
        model,
        history_model,
    ) -> int:
        main_table = self._q(model._meta.db_table)
        history_table = self._q(history_model._meta.db_table)
        pk_col = self._q(model._meta.pk.column)

        sql = f"""
        SELECT COUNT(*)
        FROM {main_table} m
        WHERE NOT EXISTS (
            SELECT 1
            FROM {history_table} h
            WHERE h.{pk_col} = m.{pk_col}
        )
        """
        cursor.execute(sql)
        return int(cursor.fetchone()[0])

    def _insert_missing_history_rows_sql(
        self,
        cursor,
        *,
        model,
        history_model,
        timestamp,
        reason: str,
    ) -> int:
        main_table = self._q(model._meta.db_table)
        history_table = self._q(history_model._meta.db_table)
        pk_col = self._q(model._meta.pk.column)
        history_pk_col = self._q(history_model._meta.pk.column)
        valid_from_col = history_model._meta.get_field("valid_from").column
        valid_to_col = history_model._meta.get_field("valid_to").column
        history_type_col = history_model._meta.get_field("history_type").column
        history_reason_col = history_model._meta.get_field("history_change_reason").column

        history_attnames = {
            field.attname for field in history_model._meta.concrete_fields
        }
        has_history_user_id = "history_user_id" in history_attnames

        copy_cols = self._history_copy_columns(model, history_model)

        insert_cols = [
            *(column.target_column for column in copy_cols),
            valid_from_col,
            valid_to_col,
            history_type_col,
            history_reason_col,
        ]
        select_exprs = [
            *(f"m.{self._q(column.source_column)}" for column in copy_cols),
            "%s",
            "NULL",
            "%s",
            "%s",
        ]
        params: List[object] = [timestamp, "+", reason]

        if has_history_user_id:
            insert_cols.append(
                self._field_column_by_attname(history_model, "history_user_id")
            )
            select_exprs.append("NULL")

        self._append_required_defaults(
            model_class=history_model,
            table_name=history_model._meta.db_table,
            insert_cols=insert_cols,
            select_exprs=select_exprs,
            params=params,
        )

        insert_cols_sql = ", ".join(self._q(col) for col in insert_cols)
        select_exprs_sql = ", ".join(select_exprs)

        sql = f"""
        WITH inserted AS (
            INSERT INTO {history_table} ({insert_cols_sql})
            SELECT {select_exprs_sql}
            FROM {main_table} m
            WHERE NOT EXISTS (
                SELECT 1
                FROM {history_table} h
                WHERE h.{pk_col} = m.{pk_col}
            )
            RETURNING {history_pk_col}
        )
        SELECT COUNT(*)
        FROM inserted
        """
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])

    def _history_type_repair_candidates_cte(self, history_model, pk_attname: str) -> str:
        history_table = self._q(history_model._meta.db_table)
        history_pk_col = self._q(history_model._meta.pk.column)
        pk_col = self._q(self._field_column_by_attname(history_model, pk_attname))
        history_type_col = self._q(history_model._meta.get_field("history_type").column)
        valid_from_col = self._q(history_model._meta.get_field("valid_from").column)

        return f"""
        WITH ranked AS (
            SELECT
                h.{history_pk_col} AS history_id,
                ROW_NUMBER() OVER (
                    PARTITION BY h.{pk_col}
                    ORDER BY h.{valid_from_col}, h.{history_pk_col}
                ) AS row_num,
                MAX(CASE WHEN h.{history_type_col} = %s THEN 1 ELSE 0 END)
                    OVER (PARTITION BY h.{pk_col}) AS has_create
            FROM {history_table} h
            WHERE h.{history_type_col} <> %s
        ),
        repair_candidates AS (
            SELECT history_id
            FROM ranked
            WHERE row_num = 1 AND has_create = 0
        )
        """

    def _count_history_type_repairs_sql(
        self,
        cursor,
        *,
        history_model,
        pk_attname: str,
    ) -> int:
        cte = self._history_type_repair_candidates_cte(history_model, pk_attname)
        sql = cte + "SELECT COUNT(*) FROM repair_candidates"
        cursor.execute(sql, ["+", "-"])
        return int(cursor.fetchone()[0])

    def _repair_history_type_markers_sql(
        self,
        cursor,
        *,
        history_model,
        pk_attname: str,
    ) -> int:
        cte = self._history_type_repair_candidates_cte(history_model, pk_attname)
        history_table = self._q(history_model._meta.db_table)
        history_pk_col = self._q(history_model._meta.pk.column)
        history_type_col = self._q(history_model._meta.get_field("history_type").column)

        sql = cte + f"""
        , updated AS (
            UPDATE {history_table} h
            SET {history_type_col} = %s
            FROM repair_candidates c
            WHERE h.{history_pk_col} = c.history_id
            RETURNING 1
        )
        SELECT COUNT(*)
        FROM updated
        """
        cursor.execute(sql, ["+", "-", "+"])
        return int(cursor.fetchone()[0])

    def _count_missing_meta_rows_sql(
        self,
        cursor,
        *,
        history_model,
        meta_model,
    ) -> int:
        history_table = self._q(history_model._meta.db_table)
        meta_table = self._q(meta_model._meta.db_table)
        history_pk_col = self._q(history_model._meta.pk.column)
        history_object_col = self._q(
            self._field_column_by_attname(meta_model, "history_object_id")
        )

        sql = f"""
        SELECT COUNT(*)
        FROM {history_table} h
        WHERE NOT EXISTS (
            SELECT 1
            FROM {meta_table} m
            WHERE m.{history_object_col} = h.{history_pk_col}
        )
        """
        cursor.execute(sql)
        return int(cursor.fetchone()[0])

    def _insert_missing_meta_rows_sql(
        self,
        cursor,
        *,
        history_model,
        meta_model,
        timestamp,
        reason: str,
    ) -> int:
        history_table = self._q(history_model._meta.db_table)
        meta_table = self._q(meta_model._meta.db_table)
        history_pk_col = self._q(history_model._meta.pk.column)
        meta_pk_col = self._q(meta_model._meta.pk.column)
        history_object_col = self._q(
            self._field_column_by_attname(meta_model, "history_object_id")
        )
        valid_from_col = history_model._meta.get_field("valid_from").column
        history_reason_col = history_model._meta.get_field(
            "history_change_reason"
        ).column
        sys_from_col = meta_model._meta.get_field("sys_from").column
        sys_to_col = meta_model._meta.get_field("sys_to").column
        meta_reason_col = meta_model._meta.get_field(
            "meta_history_change_reason"
        ).column
        meta_type_col = meta_model._meta.get_field("meta_history_type").column

        meta_attnames = {field.attname for field in meta_model._meta.concrete_fields}
        has_meta_user_id = "meta_history_user_id" in meta_attnames

        copy_cols = self._meta_copy_columns(history_model, meta_model)

        insert_cols = [
            *(column.target_column for column in copy_cols),
            sys_from_col,
            sys_to_col,
            meta_reason_col,
            meta_type_col,
            self._field_column_by_attname(meta_model, "history_object_id"),
        ]
        select_exprs = [
            *(f"h.{self._q(column.source_column)}" for column in copy_cols),
            f"COALESCE(h.{self._q(valid_from_col)}, %s)",
            "NULL",
            f"COALESCE(NULLIF(h.{self._q(history_reason_col)}, ''), %s)",
            "%s",
            f"h.{history_pk_col}",
        ]
        params: List[object] = [timestamp, reason, "+"]

        if has_meta_user_id:
            insert_cols.append(
                self._field_column_by_attname(meta_model, "meta_history_user_id")
            )
            select_exprs.append("NULL")

        self._append_required_defaults(
            model_class=meta_model,
            table_name=meta_model._meta.db_table,
            insert_cols=insert_cols,
            select_exprs=select_exprs,
            params=params,
        )

        insert_cols_sql = ", ".join(self._q(col) for col in insert_cols)
        select_exprs_sql = ", ".join(select_exprs)

        sql = f"""
        WITH inserted AS (
            INSERT INTO {meta_table} ({insert_cols_sql})
            SELECT {select_exprs_sql}
            FROM {history_table} h
            WHERE NOT EXISTS (
                SELECT 1
                FROM {meta_table} m
                WHERE m.{history_object_col} = h.{history_pk_col}
            )
            RETURNING {meta_pk_col}
        )
        SELECT COUNT(*)
        FROM inserted
        """
        cursor.execute(sql, params)
        return int(cursor.fetchone()[0])

    def _create_missing_history_rows_via_orm(
        self,
        *,
        model,
        history_model,
        pk_lookup: str,
        order_field: str,
        chunk_size: int,
        timestamp,
        reason: str,
    ) -> int:
        created = 0
        history_manager = model.history
        missing_history_qs = _missing_history_queryset(
            model=model,
            history_model=history_model,
            pk_lookup=pk_lookup,
        )

        for obj in missing_history_qs.order_by(order_field).iterator(
            chunk_size=chunk_size
        ):
            obj._history_date = timestamp
            obj._history_change_reason = reason
            create_standard_history_record(
                history_manager=history_manager,
                instance=obj,
                history_type="+",
            )
            created += 1

        return created

    def handle(self, *args, **options):
        timestamp = self._resolve_timestamp(options["timestamp"])
        chunk_size = options["chunk_size"]
        reason = options["reason"]
        dry_run = options["dry_run"]

        if chunk_size <= 0:
            raise CommandError("--chunk-size must be a positive integer")

        future_backfill = self._is_future_backfill(timestamp)

        summary: Dict[str, List[Dict[str, object]] | str | int | bool] = {
            "timestamp": timestamp.isoformat(),
            "chunk_size": chunk_size,
            "reason": reason,
            "dry_run": dry_run,
            "created_total": 0,
            "repaired_history_type_total": 0,
            "created_meta_total": 0,
            "processed_models": [],
            "skipped_models": [],
        }

        for model, history_model, meta_model in sorted(
            _iter_tracked_models(), key=lambda item: item[0]._meta.label_lower
        ):
            self._validate_sql_schema(
                model=model,
                history_model=history_model,
                meta_model=meta_model,
            )

            label = model._meta.label
            pk_attname = model._meta.pk.attname
            pk_name = model._meta.pk.name
            main_count = model._default_manager.count()
            history_count = history_model._default_manager.count()
            meta_count = meta_model._default_manager.count()

            if main_count == 0:
                summary["skipped_models"].append(
                    {
                        "model": label,
                        "reason": "main_table_empty",
                        "main_count": 0,
                        "history_count": 0,
                        "meta_count": 0,
                    }
                )
                self.stdout.write(f"Skipping {label}: main table is empty")
                continue

            created = 0
            repaired_history_type = 0
            created_meta = 0

            if dry_run:
                with connection.cursor() as cursor:
                    created = self._count_missing_history_rows_sql(
                        cursor,
                        model=model,
                        history_model=history_model,
                    )
                    repaired_history_type = self._count_history_type_repairs_sql(
                        cursor,
                        history_model=history_model,
                        pk_attname=pk_attname,
                    )
                    created_meta = self._count_missing_meta_rows_sql(
                        cursor,
                        history_model=history_model,
                        meta_model=meta_model,
                    )
            else:
                with transaction.atomic():
                    use_orm_history_backfill = future_backfill or self._history_backfill_requires_orm(
                        model
                    )
                    if use_orm_history_backfill and self._orm_backfill_supported(
                        model=model,
                        history_model=history_model,
                        meta_model=meta_model,
                    ):
                        if not future_backfill:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"{label}: model spans multiple concrete tables; "
                                    "using ORM history backfill path."
                                )
                            )
                        with suppress_main_table_sync(), suppress_meta_sys_to_chaining():
                            created = self._create_missing_history_rows_via_orm(
                                model=model,
                                history_model=history_model,
                                pk_lookup=pk_attname,
                                order_field=pk_name,
                                chunk_size=chunk_size,
                                timestamp=timestamp,
                                reason=reason,
                            )

                        with connection.cursor() as cursor:
                            repaired_history_type = self._repair_history_type_markers_sql(
                                cursor,
                                history_model=history_model,
                                pk_attname=pk_attname,
                            )
                            created_meta = self._insert_missing_meta_rows_sql(
                                cursor,
                                history_model=history_model,
                                meta_model=meta_model,
                                timestamp=timestamp,
                                reason=reason,
                            )
                    else:
                        if future_backfill or use_orm_history_backfill:
                            reason_text = (
                                "future timestamp requested"
                                if future_backfill
                                else "model spans multiple concrete tables"
                            )
                            self.stdout.write(
                                self.style.WARNING(
                                    f"{label}: {reason_text}, but runtime schema "
                                    "does not support the ORM path; using SQL "
                                    "backfill path instead."
                                )
                            )
                        with connection.cursor() as cursor:
                            created = self._insert_missing_history_rows_sql(
                                cursor,
                                model=model,
                                history_model=history_model,
                                timestamp=timestamp,
                                reason=reason,
                            )
                            repaired_history_type = self._repair_history_type_markers_sql(
                                cursor,
                                history_model=history_model,
                                pk_attname=pk_attname,
                            )
                            created_meta = self._insert_missing_meta_rows_sql(
                                cursor,
                                history_model=history_model,
                                meta_model=meta_model,
                                timestamp=timestamp,
                                reason=reason,
                            )

            summary["processed_models"].append(
                {
                    "model": label,
                    "main_count": main_count,
                    "history_count_before": history_count,
                    "meta_count_before": meta_count,
                    "created_history_rows": created,
                    "repaired_history_type_rows": repaired_history_type,
                    "created_meta_rows": created_meta,
                }
            )
            summary["created_total"] += created
            summary["repaired_history_type_total"] += repaired_history_type
            summary["created_meta_total"] += created_meta
            action = "Would process" if dry_run else "Processed"
            self.stdout.write(
                f"{action} {label}: "
                f"history+={created}, repaired+={repaired_history_type}, meta+={created_meta}"
            )

        self.stdout.write("BITEMPORAL_BACKFILL_SUMMARY_START")
        self.stdout.write(json.dumps(summary, sort_keys=True))
        self.stdout.write("BITEMPORAL_BACKFILL_SUMMARY_END")
