import logging
import threading
import time
from datetime import datetime, timezone
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
      it finishes (SUCCESS / ERROR / ABORTED / CANCELLED).
    * **Thread-safe**: All access is serialised via a ``threading.Lock``.
    * **Transient**: The store is empty on server start.  Stale DB rows
      in IN_PROGRESS are reset to ABORTED by ``model_registration`` during
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

        On first registration, stamps ``started_at_monotonic`` (a
        ``time.monotonic()`` reading used for safe age math — wall-clock
        time can jump backwards) and ``started_at_iso`` (a UTC ISO-8601
        string for display in the operator UI / API report). A re-entrant
        registration of the same ``record_id`` keeps the original start
        timestamps so the visible "running time" reflects when the
        calculation actually began, not when its hook was last touched.
        """
        if not record_id:
            return

        with cls._lock:
            existing = cls._state_map.get(record_id, {}) if isinstance(
                cls._state_map.get(record_id), dict
            ) else {}
            now_monotonic = existing.get("started_at_monotonic")
            now_iso = existing.get("started_at_iso")
            if not isinstance(now_monotonic, float):
                now_monotonic = time.monotonic()
                now_iso = datetime.now(timezone.utc).isoformat()
            cls._state_map[record_id] = {
                "record_id": record_id,
                "record": record or record_id,
                "calculation_id": calculation_id or "",
                "model_label": model_label or "",
                "record_pk": str(record_pk) if record_pk is not None else "",
                "task_id": existing.get("task_id", ""),
                "started_at_monotonic": now_monotonic,
                "started_at_iso": now_iso or datetime.now(timezone.utc).isoformat(),
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
        calculation_id = None
        with cls._lock:
            entry = cls._state_map.get(record_id)
            if isinstance(entry, dict):
                entry["task_id"] = str(task_id)
                calculation_id = entry.get("calculation_id") or None
        # Mirror into the cluster cancel index so other processes (the
        # backend running cancel()) can discover this node's task_id.
        # Best-effort: a Redis failure never breaks registration.
        if calculation_id:
            try:
                from lex.core.cancellation import cluster_cancel_index

                cluster_cancel_index.register_task(
                    calculation_id, record_id, str(task_id)
                )
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "Failed to mirror task_id into cluster cancel index for %s",
                    record_id,
                    exc_info=True,
                )

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
    def list_active(
        cls,
        *,
        older_than_seconds: Optional[float] = None,
    ) -> List[Dict[str, Any]]:
        """Return every active entry, optionally filtered by age.

        Each returned dict carries the stored fields plus a freshly
        computed ``age_seconds`` (non-negative float) so callers do not
        need to know about the monotonic clock. Entries are sorted
        oldest-first — the natural order for an operator looking for
        stuck work.

        ``older_than_seconds``:
            * ``None`` (default) — return every entry.
            * ``>= 0`` — return only entries whose ``age_seconds`` is
              **greater than or equal to** the threshold. ``0`` therefore
              still returns everything; ``60`` returns calculations that
              have been running at least a minute.
            * Negative values raise ``ValueError`` — a negative threshold
              is almost always a bug at the call site (e.g. an
              uninitialised setting) and silently returning the whole
              store would mask it.
        """
        if older_than_seconds is not None and older_than_seconds < 0:
            raise ValueError(
                f"older_than_seconds must be >= 0, got {older_than_seconds!r}"
            )

        now = time.monotonic()
        with cls._lock:
            entries = [
                dict(e) for e in cls._state_map.values() if isinstance(e, dict)
            ]

        enriched: List[Dict[str, Any]] = []
        for entry in entries:
            started = entry.get("started_at_monotonic")
            if isinstance(started, (int, float)):
                age = max(0.0, float(now - started))
            else:
                # Legacy entry written before started_at tracking was added —
                # treat as age=0 rather than crashing the operator endpoint.
                age = 0.0
            entry["age_seconds"] = age
            if older_than_seconds is None or age >= older_than_seconds:
                enriched.append(entry)

        enriched.sort(key=lambda e: e.get("age_seconds", 0.0), reverse=True)
        return enriched

    @classmethod
    def clear(cls, record_id: str) -> None:
        """Remove a record from the active-calculations store (terminal state reached)."""
        if not record_id:
            return
        calculation_id = None
        with cls._lock:
            entry = cls._state_map.pop(record_id, None)
            if isinstance(entry, dict):
                calculation_id = entry.get("calculation_id") or None
        if calculation_id:
            try:
                from lex.core.cancellation import cluster_cancel_index

                cluster_cancel_index.unregister_task(calculation_id, record_id)
            except Exception:  # pragma: no cover — defensive
                logger.warning(
                    "Failed to remove task_id from cluster cancel index for %s",
                    record_id,
                    exc_info=True,
                )

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
        (SUCCESS / ERROR / ABORTED / CANCELLED) are pruned from the store and excluded
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
