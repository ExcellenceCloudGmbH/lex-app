import json
from typing import Dict, List

from django.apps import apps
from django.core.management.base import BaseCommand
from django.db import transaction

from lex.core.models.CalculationModel import CalculationModel


TRUE_VALUES = {"true", "1", "t", "yes", "y"}
FALSE_VALUES = {"false", "0", "f", "no", "n"}


def _normalize_value(value):
    if value is True or value == 1:
        return CalculationModel.SUCCESS
    if value is False or value == 0:
        return CalculationModel.NOT_CALCULATED
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in TRUE_VALUES:
            return CalculationModel.SUCCESS
        if lowered in FALSE_VALUES:
            return CalculationModel.NOT_CALCULATED
    return None


class Command(BaseCommand):
    help = (
        "Normalize boolean-like `is_calculated` values on CalculationModel subclasses "
        "to SUCCESS/ERROR."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=500,
            help="Iterator chunk size (default: 500).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report planned changes without writing updates.",
        )

    def handle(self, *args, **options):
        chunk_size = options["chunk_size"]
        dry_run = options["dry_run"]
        if chunk_size <= 0:
            chunk_size = 500

        summary: Dict[str, List[Dict[str, object]] | int | bool] = {
            "dry_run": dry_run,
            "chunk_size": chunk_size,
            "updated_total": 0,
            "models": [],
        }

        for model in apps.get_models():
            if model._meta.abstract or model._meta.proxy:
                continue
            if not issubclass(model, CalculationModel):
                continue
            try:
                field = model._meta.get_field("is_calculated")
            except Exception:
                continue
            if field is None:
                continue

            updates = []
            checked = 0
            queryset = model._default_manager.all().only(model._meta.pk.name, "is_calculated")
            for obj in queryset.iterator(chunk_size=chunk_size):
                checked += 1
                normalized = _normalize_value(getattr(obj, "is_calculated", None))
                if normalized is None:
                    continue
                if obj.is_calculated == normalized:
                    continue
                obj.is_calculated = normalized
                updates.append(obj)

            if updates and not dry_run:
                with transaction.atomic():
                    model._default_manager.bulk_update(updates, ["is_calculated"], batch_size=chunk_size)

            updated_count = len(updates)
            summary["updated_total"] += updated_count
            summary["models"].append(
                {
                    "model": model._meta.label,
                    "checked": checked,
                    "updated": updated_count,
                }
            )
            self.stdout.write(
                f"{'Would normalize' if dry_run else 'Normalized'} {model._meta.label}: "
                f"{updated_count} row(s)"
            )

        self.stdout.write("IS_CALCULATED_NORMALIZATION_SUMMARY_START")
        self.stdout.write(json.dumps(summary, sort_keys=True))
        self.stdout.write("IS_CALCULATED_NORMALIZATION_SUMMARY_END")
