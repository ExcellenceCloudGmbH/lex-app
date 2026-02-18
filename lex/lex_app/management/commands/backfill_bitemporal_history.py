import json
from typing import Dict, Iterable, List, Tuple

from django.apps import apps
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.conf import settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime


CONTROL_FIELDS = {
    "history_id",
    "valid_from",
    "valid_to",
    "history_type",
    "history_change_reason",
    "history_user",
    "history_user_id",
}

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


def _build_history_row(instance, history_model, timestamp, reason):
    history_row = history_model()

    history_fields = {field.attname for field in history_model._meta.fields}
    for field in instance._meta.fields:
        if field.attname in CONTROL_FIELDS or field.name in CONTROL_FIELDS:
            continue
        if field.attname in history_fields:
            setattr(history_row, field.attname, getattr(instance, field.attname))
        elif field.name in history_fields:
            setattr(history_row, field.name, getattr(instance, field.name))

    history_row.valid_from = timestamp
    history_row.valid_to = None
    history_row.history_type = "+"
    history_row.history_change_reason = reason
    if hasattr(history_row, "history_user_id"):
        history_row.history_user_id = None

    # Ensures MetaHistory sys_from uses the same migration timestamp.
    history_row._history_date = timestamp
    history_row._history_change_reason = reason
    return history_row


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
            "processed_models": [],
            "skipped_models": [],
        }

        for model, history_model, meta_model in sorted(
            _iter_tracked_models(), key=lambda item: item[0]._meta.label_lower
        ):
            label = model._meta.label
            main_count = model._default_manager.count()
            history_count = history_model._default_manager.count()
            meta_count = meta_model._default_manager.count()

            if history_count > 0 or meta_count > 0:
                summary["skipped_models"].append(
                    {
                        "model": label,
                        "reason": "history_or_meta_not_empty",
                        "main_count": main_count,
                        "history_count": history_count,
                        "meta_count": meta_count,
                    }
                )
                self.stdout.write(
                    f"Skipping {label}: history/meta already populated "
                    f"(history={history_count}, meta={meta_count})"
                )
                continue

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
            if dry_run:
                created = main_count
            else:
                with transaction.atomic():
                    queryset = model._default_manager.all().order_by(model._meta.pk.name)
                    for obj in queryset.iterator(chunk_size=chunk_size):
                        history_row = _build_history_row(
                            obj,
                            history_model=history_model,
                            timestamp=timestamp,
                            reason=reason,
                        )
                        history_row.save()
                        created += 1

            summary["processed_models"].append(
                {
                    "model": label,
                    "main_count": main_count,
                    "created_history_rows": created,
                }
            )
            summary["created_total"] += created
            action = "Would backfill" if dry_run else "Backfilled"
            self.stdout.write(f"{action} {label}: {created} rows")

        self.stdout.write("BITEMPORAL_BACKFILL_SUMMARY_START")
        self.stdout.write(json.dumps(summary, sort_keys=True))
        self.stdout.write("BITEMPORAL_BACKFILL_SUMMARY_END")
