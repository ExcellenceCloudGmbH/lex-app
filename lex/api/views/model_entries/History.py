"""
History API View — Bitemporal timeline endpoint.

Returns the full history of a record, optionally time-travelling to
a specific system-time snapshot using the ``?as_of`` query parameter.
"""

from django.db.models import Prefetch
from lex.api.utils.temporal import parse_as_of_datetime
from lex.api.views.permissions.UserPermission import UserPermission
from rest_framework import status
from rest_framework.generics import ListAPIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework_api_key.permissions import HasAPIKey


class HistoryModelEntry(ListAPIView):
    """
    GET /api/{model}/{pk}/history/?as_of=<ISO datetime>

    Without ``as_of``: returns all Level 1 history records (valid time).
    With ``as_of``:    returns Level 2 meta-history snapshot at that system time.
    """

    permission_classes = [HasAPIKey | IsAuthenticated, UserPermission]

    def list(self, request, *args, **kwargs):
        model_container = kwargs["model_container"]
        model_class = model_container.model_class
        pk = kwargs["pk"]

        if not hasattr(model_class, "history"):
            return Response(
                {"error": f"Model {model_class.__name__} does not track history."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        HistoryModel = model_class.history.model
        pk_name = model_class._meta.pk.name
        serializers_map = (
            model_container.get_serializers_map()
            if hasattr(model_container, "get_serializers_map")
            else model_container.serializers_map
        )
        serializer_class = serializers_map.get("default")
        serializer_context = {"request": request, "view": self} if serializer_class else None

        # ── Fetch records ──
        history_qs = self._get_history_queryset(
            request, HistoryModel, pk_name, pk
        )

        # ── Serialize ──
        data = []
        for record in history_qs:
            entry = self._serialize_record(
                record,
                serializer_class=serializer_class,
                serializer_context=serializer_context,
            )
            data.append(entry)

        return Response(data)

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _get_history_queryset(self, request, HistoryModel, pk_name, pk):
        """Build the queryset, switching between valid-time and system-time."""
        as_of_param = request.query_params.get("as_of")

        if as_of_param:
            as_of_date = parse_as_of_datetime(as_of_param)
            if as_of_date:
                from lex.core.services.Bitemporal import get_queryset_as_of

                try:
                    qs = get_queryset_as_of(HistoryModel, as_of_date)
                    qs = qs.filter(**{pk_name: pk}).order_by("-valid_from")
                    return self._optimize_history_queryset(qs)
                except ValueError:
                    return HistoryModel.objects.none()

        qs = HistoryModel.objects.filter(**{pk_name: pk}).order_by(
            "-valid_from", "-history_id"
        )
        return self._optimize_history_queryset(qs)

    def _optimize_history_queryset(self, queryset):
        model = queryset.model

        try:
            model._meta.get_field("history_user")
            queryset = queryset.select_related("history_user")
        except Exception:
            pass

        try:
            relation = model._meta.get_field("meta_history")
            meta_model = relation.related_model
            queryset = queryset.prefetch_related(
                Prefetch(
                    "meta_history",
                    queryset=meta_model.objects.order_by("-sys_from"),
                )
            )
        except Exception:
            pass

        return queryset

    def _serialize_record(self, record, serializer_class=None, serializer_context=None):
        """Serialize a single history or meta-history record."""

        # Determine effective record (meta wraps history, but copies fields)
        effective_record = record

        # ── User info ──
        user_info = self._get_user_info(effective_record)

        # ── Snapshot data ──
        snapshot = self._get_snapshot(
            effective_record,
            serializer_class=serializer_class,
            serializer_context=serializer_context,
        )

        # ── System history (Level 2 meta records) ──
        system_history = self._get_system_history(record)

        return {
            "history_id": record.history_id,
            "valid_from": record.valid_from,
            "valid_to": record.valid_to,
            "history_type": record.history_type,
            "change_reason": record.history_change_reason,
            "user": user_info,
            "snapshot": snapshot,
            "system_history": system_history,
        }

    def _get_user_info(self, record):
        """Extract user information from a history record."""
        h_user_id = getattr(record, "history_user_id", None)
        if not h_user_id:
            return None

        h_user = getattr(record, "history_user", None)
        if not h_user:
            return {"id": h_user_id, "name": "Unknown User"}

        name = (
            f"{getattr(h_user, 'first_name', '')} "
            f"{getattr(h_user, 'last_name', '')}"
        ).strip()

        if not name:
            name = (
                getattr(h_user, "username", "")
                or getattr(h_user, "email", "")
                or str(h_user).strip()
            )

        return {
            "id": h_user.id,
            "email": getattr(h_user, "email", ""),
            "name": name,
        }

    def _get_snapshot(self, record, serializer_class=None, serializer_context=None):
        """Serialize the data fields of a history record."""
        if serializer_class:
            serializer = serializer_class(record, context=serializer_context or {})
            return serializer.data

        # Fallback: manual field serialization
        CONTROL_FIELDS = {
            "history_id", "valid_from", "valid_to", "history_type",
            "history_change_reason", "history_user", "history_user_id",
            "history_relation",
            "meta_history_id", "sys_from", "sys_to", "meta_history_type",
            "meta_history_change_reason", "meta_history_user",
            "meta_history_user_id", "history_object", "history_object_id",
            "meta_task_name", "meta_task_status", "instance_type",
        }

        snapshot = {}
        for field in record.__class__._meta.fields:
            if field.name in CONTROL_FIELDS:
                continue
            val = getattr(record, field.name)
            if hasattr(val, "isoformat"):
                val = val.isoformat()
            elif not isinstance(val, (str, int, float, bool, type(None), list, dict)):
                val = str(val)
            snapshot[field.name] = val

        return snapshot

    def _get_system_history(self, record):
        """Fetch Level 2 meta-history for a history record."""
        if not hasattr(record, "meta_history"):
            return []

        return [
            {
                "sys_from": meta.sys_from,
                "sys_to": meta.sys_to,
                "task_status": getattr(meta, "meta_task_status", "NONE"),
                "task_name": getattr(meta, "meta_task_name", None),
                "change_reason": getattr(
                    meta, "meta_history_change_reason", None
                ),
            }
            for meta in record.meta_history.all()
        ]
