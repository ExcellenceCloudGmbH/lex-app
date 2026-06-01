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
      it finishes (SUCCESS / ERROR / ABORTED).
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

        If an entry already exists (e.g. the framework re-registers the
        same record from a deeper layer of the dispatch path), an
        already-set ``cancel_requested`` flag is **preserved** — a
        cancellation that landed during the brief window before the
        registration completed must not be silently dropped.
        """
        if not record_id:
            return

        with cls._lock:
            existing = cls._state_map.get(record_id, {})
            entry = {
                "record_id": record_id,
                "record": record or record_id,
                "calculation_id": calculation_id or "",
                "model_label": model_label or "",
                "record_pk": str(record_pk) if record_pk is not None else "",
            }
            # Preserve cancel-request state across re-registrations so a
            # cancel that raced the dispatch is honoured rather than
            # erased by the next ``mark_in_progress`` call.
            if existing.get("cancel_requested") == "true":
                entry["cancel_requested"] = "true"
                requested_by = existing.get("cancel_requested_by")
                if isinstance(requested_by, str) and requested_by:
                    entry["cancel_requested_by"] = requested_by
            cls._state_map[record_id] = entry

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

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------
    #
    # ``request_cancel`` is the **only** mutator that flips the
    # ``cancel_requested`` flag on a tracked entry.  Cooperative
    # cancellation works like this:
    #
    # 1. A request handler (``CancelCalculation`` REST view, or any
    #    framework code) calls :meth:`request_cancel` with the
    #    ``record_id`` of an active calculation.
    # 2. The flag is set on the in-memory entry.  No DB write — the
    #    record's terminal state is owned by the calculation itself.
    # 3. The running ``calculate()`` body polls
    #    :meth:`CalculationModel.check_cancelled` at safe interruption
    #    points; it raises :class:`CalculationCancelled` once the flag
    #    is observed.
    # 4. ``execute_calculation_sync`` catches that exception and settles
    #    the row in ``ABORTED`` (instead of ``ERROR``).  ``clear`` is
    #    called as part of the terminal-state transition, which also
    #    drops the ``cancel_requested`` flag — a follow-up retry starts
    #    from a clean slate.
    #
    # If ``calculate()`` never polls, the state-guard at the SUCCESS
    # write in ``execute_calculation_sync`` still flips the terminal
    # state to ``ABORTED`` so the UI reflects the user's intent.  The
    # running thread is allowed to finish (Python cannot safely kill
    # threads), but its result is discarded.

    @classmethod
    def request_cancel(
        cls,
        record_id: str,
        *,
        requested_by: Optional[str] = None,
    ) -> bool:
        """Flag an active calculation for cooperative cancellation.

        Returns ``True`` if the record was registered as active (and the
        flag was therefore set), ``False`` if no active entry exists —
        the caller can treat ``False`` as "nothing to cancel".
        """
        if not record_id:
            return False
        with cls._lock:
            entry = cls._state_map.get(record_id)
            if entry is None:
                return False
            entry["cancel_requested"] = "true"
            if requested_by:
                entry["cancel_requested_by"] = str(requested_by)
            return True

    @classmethod
    def is_cancel_requested(cls, record_id: str) -> bool:
        """Return ``True`` if cancellation was requested for ``record_id``."""
        if not record_id:
            return False
        with cls._lock:
            entry = cls._state_map.get(record_id, {})
        return entry.get("cancel_requested") == "true"

    @classmethod
    def get_cancel_requested_by(cls, record_id: str) -> Optional[str]:
        """Return the actor that requested the cancel (or ``None``)."""
        if not record_id:
            return None
        with cls._lock:
            entry = cls._state_map.get(record_id, {})
        requested_by = entry.get("cancel_requested_by")
        if isinstance(requested_by, str) and requested_by:
            return requested_by
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
        (SUCCESS / ERROR / ABORTED) are pruned from the store and excluded
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

