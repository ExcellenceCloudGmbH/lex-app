import json
from typing import Dict, Iterable, List, Tuple

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Exists, OuterRef
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from lex.core.services.bitemporal_signals import suppress_main_table_sync

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


def _missing_history_queryset(model, history_model, pk_name: str):
    history_exists = history_model._default_manager.filter(
        **{pk_name: OuterRef(pk_name)}
    )
    return model._default_manager.annotate(
        _has_history=Exists(history_exists)
    ).filter(_has_history=False)


class Command(BaseCommand):
    help = (
        "Backfill bitemporal history (History + MetaHistory) through ORM/signal paths "
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
            help="Iterator chunk size for main-table scan (default: 500).",
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

    def handle(self, *args, **options):
        timestamp = self._resolve_timestamp(options["timestamp"])
        chunk_size = options["chunk_size"]
        reason = options["reason"]
        dry_run = options["dry_run"]

        if chunk_size <= 0:
            raise CommandError("--chunk-size must be a positive integer")

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
            label = model._meta.label
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
            missing_history_qs = _missing_history_queryset(
                model=model,
                history_model=history_model,
                pk_name=pk_name,
            )

            if dry_run:
                created = missing_history_qs.count()
                repaired_history_type = self._count_history_type_repairs(
                    history_model=history_model,
                    pk_name=pk_name,
                    chunk_size=chunk_size,
                )
                created_meta = history_model._default_manager.filter(
                    meta_history__isnull=True
                ).count()
            else:
                with transaction.atomic():
                    with suppress_main_table_sync():
                        history_manager = model.history
                        for obj in missing_history_qs.order_by(pk_name).iterator(
                            chunk_size=chunk_size
                        ):
                            obj._history_date = timestamp
                            obj._history_change_reason = reason
                            history_manager.create_historical_record(obj, "+")
                            created += 1

                        repaired_history_type = self._repair_history_type_markers(
                            history_model=history_model,
                            pk_name=pk_name,
                            chunk_size=chunk_size,
                        )

                        meta_manager = getattr(history_model, "meta_history", None)
                        if meta_manager:
                            missing_meta_qs = (
                                history_model._default_manager.filter(
                                    meta_history__isnull=True
                                )
                                .order_by(pk_name, "history_id")
                            )
                            for history_row in missing_meta_qs.iterator(
                                chunk_size=chunk_size
                            ):
                                history_row._history_date = (
                                    getattr(history_row, "valid_from", None) or timestamp
                                )
                                history_row._history_change_reason = (
                                    getattr(
                                        history_row, "history_change_reason", None
                                    )
                                    or reason
                                )
                                meta_manager.create_historical_record(history_row, "+")
                                created_meta += 1

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

    def _iter_pks_without_create_marker(
        self, history_model, pk_name: str, chunk_size: int
    ):
        qs = (
            history_model._default_manager.exclude(history_type="+")
            .values_list(pk_name, flat=True)
            .distinct()
            .order_by(pk_name)
        )
        return qs.iterator(chunk_size=chunk_size)

    def _count_history_type_repairs(
        self, history_model, pk_name: str, chunk_size: int
    ) -> int:
        repairs = 0
        for pk_val in self._iter_pks_without_create_marker(
            history_model, pk_name, chunk_size
        ):
            if history_model._default_manager.filter(
                **{pk_name: pk_val}, history_type="+"
            ).exists():
                continue
            first_non_delete = (
                history_model._default_manager.filter(**{pk_name: pk_val})
                .exclude(history_type="-")
                .order_by("valid_from", "history_id")
                .first()
            )
            if first_non_delete and first_non_delete.history_type != "+":
                repairs += 1
        return repairs

    def _repair_history_type_markers(
        self, history_model, pk_name: str, chunk_size: int
    ) -> int:
        repairs = 0
        for pk_val in self._iter_pks_without_create_marker(
            history_model, pk_name, chunk_size
        ):
            if history_model._default_manager.filter(
                **{pk_name: pk_val}, history_type="+"
            ).exists():
                continue
            first_non_delete = (
                history_model._default_manager.filter(**{pk_name: pk_val})
                .exclude(history_type="-")
                .order_by("valid_from", "history_id")
                .first()
            )
            if first_non_delete and first_non_delete.history_type != "+":
                history_model._default_manager.filter(
                    history_id=first_non_delete.history_id
                ).update(history_type="+")
                repairs += 1
        return repairs
