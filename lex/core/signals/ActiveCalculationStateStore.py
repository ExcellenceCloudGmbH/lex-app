import logging
from typing import Any, Dict, List, Optional, Tuple

from django.apps import apps
from django.core.cache import cache

from lex.core.models.CalculationModel import CalculationModel

logger = logging.getLogger(__name__)


class ActiveCalculationStateStore:
    """
    Stores active calculations for websocket reconciliation.

    The store is intentionally cache-backed so reconnecting websocket clients can
    receive authoritative in-progress state even if they missed earlier events.
    """

    CACHE_KEY = "active_calculation_states_v1"

    @classmethod
    def mark_in_progress(
        cls,
        *,
        record_id: str,
        calculation_id: Optional[str],
        record: Optional[str],
        model_label: Optional[str] = None,
        record_pk: Optional[Any] = None,
    ) -> None:
        if not record_id:
            return

        state_map = cls._load_state_map()
        state_map[record_id] = {
            "record_id": record_id,
            "record": record or record_id,
            "calculation_id": calculation_id or "",
            "model_label": model_label or "",
            "record_pk": str(record_pk) if record_pk is not None else "",
        }
        cls._save_state_map(state_map)

    @classmethod
    def clear(cls, record_id: str) -> None:
        if not record_id:
            return
        state_map = cls._load_state_map()
        if record_id in state_map:
            del state_map[record_id]
            cls._save_state_map(state_map)

    @classmethod
    def get_calculation_id(cls, record_id: str) -> Optional[str]:
        state_map = cls._load_state_map()
        entry = state_map.get(record_id, {})
        calculation_id = entry.get("calculation_id")
        if isinstance(calculation_id, str) and calculation_id:
            return calculation_id
        return None

    @classmethod
    def snapshot(cls) -> List[Dict[str, str]]:
        """
        Return active calculations and prune stale cache entries.
        """
        state_map = cls._load_state_map()
        if not state_map:
            return []

        pruned_map: Dict[str, Dict[str, str]] = {}
        for record_id, entry in state_map.items():
            if cls._is_entry_in_progress(entry):
                pruned_map[record_id] = entry

        if pruned_map != state_map:
            cls._save_state_map(pruned_map)

        return [
            {
                "record_id": entry.get("record_id", ""),
                "record": entry.get("record", entry.get("record_id", "")),
                "calculation_id": entry.get("calculation_id", ""),
            }
            for entry in sorted(pruned_map.values(), key=lambda item: item.get("record_id", ""))
        ]

    @classmethod
    def _is_entry_in_progress(cls, entry: Dict[str, str]) -> bool:
        model_class, record_pk = cls._resolve_model_and_pk(entry)
        if model_class is None or record_pk is None:
            return False

        try:
            instance = model_class.objects.filter(pk=record_pk).only("is_calculated").first()
        except Exception:
            logger.exception(
                "Failed to validate active calculation entry",
                extra={"entry": entry},
            )
            return False

        return (
            instance is not None
            and getattr(instance, "is_calculated", None) == CalculationModel.IN_PROGRESS
        )

    @classmethod
    def _resolve_model_and_pk(
        cls, entry: Dict[str, str]
    ) -> Tuple[Optional[type], Optional[str]]:
        record_id = entry.get("record_id", "")
        model_label = entry.get("model_label", "")
        record_pk = entry.get("record_pk")

        app_label: Optional[str] = None
        model_name: Optional[str] = None
        if model_label and "." in model_label:
            app_label, model_name = model_label.split(".", 1)

        if not model_name and record_id:
            model_name, record_pk_from_id = cls._split_record_id(record_id)
            if not record_pk:
                record_pk = record_pk_from_id

        if not model_name or not record_pk:
            return None, None

        model_class: Optional[type] = None
        if app_label:
            try:
                model_class = apps.get_model(app_label, model_name)
            except Exception:
                model_class = None

        if model_class is None:
            model_class = cls._find_model_by_name(model_name)

        if model_class is None:
            return None, None

        if not issubclass(model_class, CalculationModel):
            return None, None

        return model_class, str(record_pk)

    @staticmethod
    def _split_record_id(record_id: str) -> Tuple[Optional[str], Optional[str]]:
        if not record_id or "_" not in record_id:
            return None, None
        model_name, record_pk = record_id.rsplit("_", 1)
        if not model_name or not record_pk:
            return None, None
        return model_name, record_pk

    @staticmethod
    def _find_model_by_name(model_name: str) -> Optional[type]:
        for model_class in apps.get_models():
            if (
                getattr(model_class, "_meta", None) is not None
                and model_class._meta.model_name == model_name
                and issubclass(model_class, CalculationModel)
            ):
                return model_class
        return None

    @classmethod
    def _load_state_map(cls) -> Dict[str, Dict[str, str]]:
        stored = cache.get(cls.CACHE_KEY, {})
        if isinstance(stored, dict):
            return stored
        return {}

    @classmethod
    def _save_state_map(cls, state_map: Dict[str, Dict[str, str]]) -> None:
        cache.set(cls.CACHE_KEY, state_map, timeout=None)
