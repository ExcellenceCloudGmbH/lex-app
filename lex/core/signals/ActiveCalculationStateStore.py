import logging
import threading
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ActiveCalculationStateStore:
    """
    Authoritative store of active (in-progress) calculations.

    The store is **in-memory** (process-global, protected by a lock) so
    that writes are immediately visible to every part of the application,
    including WebSocket consumers that handle reconnection reconciliation.

    Previous implementation used Django's DatabaseCache, but that caused a
    critical bug: cache writes performed inside ``transaction.atomic()``
    were invisible to other database connections (like the ASGI WebSocket
    handler) until the transaction committed.  Since calculations run
    inside atomic blocks, the reconciliation snapshot would miss entries
    that were written but not yet committed — causing child calculations
    (and sometimes parent calculations) to lose their state on page
    refresh.

    Design principles
    -----------------
    * **Write-through**: ``mark_in_progress`` / ``clear`` are the *only* mutators.
    * **No DB queries during snapshot**: The store is the single source of
      truth.  Entries are added when a calculation starts and removed when
      it finishes (SUCCESS / ERROR / CANCELLED).
    * **Thread-safe**: All access is serialised via a ``threading.Lock``.
    * **Transient**: The store is empty on server start.  Stale DB rows
      in IN_PROGRESS are reset to CANCELLED by ``model_registration`` during
      startup.
    """

    _lock = threading.Lock()
    _state_map: Dict[str, Dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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
        """Register a record as having an active calculation.

        Preserves any previously-stored ``task_id`` for the same
        ``record_id`` so a re-entrant ``calculate_hook`` invocation does
        not lose the Celery task handle needed for cancellation.
        """
        if not record_id:
            return

        with cls._lock:
            existing = cls._state_map.get(record_id, {}) if isinstance(
                cls._state_map.get(record_id), dict
            ) else {}
            cls._state_map[record_id] = {
                "record_id": record_id,
                "record": record or record_id,
                "calculation_id": calculation_id or "",
                "model_label": model_label or "",
                "record_pk": str(record_pk) if record_pk is not None else "",
                "task_id": existing.get("task_id", ""),
            }

    # ------------------------------------------------------------------
    # Cancellation support
    # ------------------------------------------------------------------

    @classmethod
    def set_task_id(cls, record_id: str, task_id: Optional[str]) -> None:
        """Attach a Celery ``task_id`` to a tracked record.

        Called by :meth:`CalculationModel.dispatch_calculation_task` right
        after Celery returns the ``AsyncResult``. The task ID is the only
        handle the cancellation endpoint has to revoke the work — without
        it, Celery-only cancel cannot terminate the worker.
        """
        if not record_id or not task_id:
            return
        with cls._lock:
            entry = cls._state_map.get(record_id)
            if isinstance(entry, dict):
                entry["task_id"] = str(task_id)

    @classmethod
    def get_task_id(cls, record_id: str) -> Optional[str]:
        with cls._lock:
            entry = cls._state_map.get(record_id, {})
        task_id = entry.get("task_id") if isinstance(entry, dict) else None
        return task_id or None

    @classmethod
    def find_descendants(cls, calculation_id: str) -> List[Dict[str, str]]:
        """Return every active entry whose ``calculation_id`` matches.

        Children dispatched from inside a parent's ``calculate()`` share
        the parent's ``calculation_id`` (set in
        ``calculate_hook``); this gives us the recursive-cancel set
        without walking a parent→child tree explicitly.
        """
        if not calculation_id:
            return []
        with cls._lock:
            return [
                dict(entry)
                for entry in cls._state_map.values()
                if isinstance(entry, dict)
                and entry.get("calculation_id") == calculation_id
            ]

    @classmethod
    def clear(cls, record_id: str) -> None:
        """Remove a record from the active-calculations store (terminal state reached)."""
        if not record_id:
            return
        with cls._lock:
            cls._state_map.pop(record_id, None)

    @classmethod
    def clear_all(cls) -> None:
        """Remove every entry (used at server startup)."""
        with cls._lock:
            cls._state_map.clear()


    @classmethod
    def get_calculation_id(cls, record_id: str) -> Optional[str]:
        with cls._lock:
            entry = cls._state_map.get(record_id, {})
        calculation_id = entry.get("calculation_id")
        if isinstance(calculation_id, str) and calculation_id:
            return calculation_id
        return None

    @classmethod
    def get_entry(cls, record_id: str) -> Dict[str, str]:
        if not record_id:
            return {}
        with cls._lock:
            entry = cls._state_map.get(record_id, {})
        if isinstance(entry, dict):
            return dict(entry)
        return {}

    @classmethod
    def snapshot(cls) -> List[Dict[str, str]]:
        """
        Return the current set of active calculations, validated against
        the database.

        This is called by ``UpdateCalculationStatusConsumer.connect()`` to
        send the reconciliation payload to a (re)connecting WebSocket
        client.

        Each entry is cross-checked against the DB so that records whose
        ``is_calculated`` field has already moved to a terminal state
        (SUCCESS / ERROR / CANCELLED) are pruned from the store and excluded
        from the payload.
        """
        from lex.core.models.CalculationModel import CalculationModel

        with cls._lock:
            entries = dict(cls._state_map)

        if not entries:
            return []

        validated: List[Dict[str, str]] = []
        stale_ids: List[str] = []

        for record_id, entry in sorted(entries.items(), key=lambda item: item[0]):
            model_class, record_pk = cls._resolve_model_and_pk(entry)
            if model_class is not None and record_pk is not None:
                try:
                    instance = (
                        model_class.objects.filter(pk=record_pk)
                        .only("is_calculated")
                        .first()
                    )
                    if instance is None or getattr(instance, "is_calculated", None) != CalculationModel.IN_PROGRESS:
                        stale_ids.append(record_id)
                        continue
                except Exception:
                    logger.exception(
                        "Failed to validate entry during snapshot, keeping it",
                        extra={"entry": entry},
                    )

            validated.append({
                "record_id": entry.get("record_id", ""),
                "record": entry.get("record", entry.get("record_id", "")),
                "calculation_id": entry.get("calculation_id", ""),
            })

        # Prune stale entries from the store
        if stale_ids:
            with cls._lock:
                for rid in stale_ids:
                    cls._state_map.pop(rid, None)

        return validated

    # ------------------------------------------------------------------
    # Startup-only validation
    # ------------------------------------------------------------------

    @classmethod
    def validate_and_prune(cls) -> None:
        """
        Prune entries whose DB row is no longer IN_PROGRESS.

        Intended to be called **once at server startup** only.
        """
        from lex.core.models.CalculationModel import CalculationModel

        with cls._lock:
            entries = dict(cls._state_map)

        if not entries:
            return

        pruned: Dict[str, Dict[str, str]] = {}
        for record_id, entry in entries.items():
            model_class, record_pk = cls._resolve_model_and_pk(entry)
            if model_class is None or record_pk is None:
                continue
            try:
                instance = (
                    model_class.objects.filter(pk=record_pk)
                    .only("is_calculated")
                    .first()
                )
                if (
                    instance is not None
                    and getattr(instance, "is_calculated", None)
                    == CalculationModel.IN_PROGRESS
                ):
                    pruned[record_id] = entry
            except Exception:
                logger.exception(
                    "Failed to validate active calculation entry during startup prune",
                    extra={"entry": entry},
                )

        with cls._lock:
            cls._state_map = pruned

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @classmethod
    def _resolve_model_and_pk(
        cls, entry: Dict[str, str]
    ) -> Tuple[Optional[type], Optional[str]]:
        from django.apps import apps
        from lex.core.models.CalculationModel import CalculationModel

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
        from django.apps import apps
        from lex.core.models.CalculationModel import CalculationModel

        for model_class in apps.get_models():
            if (
                getattr(model_class, "_meta", None) is not None
                and model_class._meta.model_name == model_name
                and issubclass(model_class, CalculationModel)
            ):
                return model_class
        return None

