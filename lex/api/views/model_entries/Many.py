import logging
import traceback

from lex.api.errors import raise_or_return
from lex.api.security import delete_allowed, edit_denial
from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend, apply_read_restriction
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin
from lex.core.exceptions import resolve_exception_traceback
from lex.core.signals.ModelMutationSignal import broadcast_model_mutation
from lex.core.security_flags import strict
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.response import Response

logger = logging.getLogger(__name__)

class ManyModelEntries(BulkAuditLogMixin, ModelEntryProviderMixin, GenericAPIView):
    filter_backends = [PrimaryKeyListFilterBackend]

    def _log_failed_bulk_request_audit_if_needed(
        self,
        *,
        action,
        targets,
        exception,
        payloads=None,
    ):
        """Persist bulk failure audits after the failed request transaction unwinds."""
        try:
            if self.flush_pending_failed_audit_logs():
                return

            if self.has_logged_failure_audit():
                return

            self.log_failed_bulk_changes(
                action,
                targets,
                payloads=payloads,
                error_traceback=resolve_exception_traceback(
                    exception,
                    fallback=traceback.format_exc(),
                ),
            )
        except Exception:
            logger.exception("Failed to write failure audit logs for bulk %s", action)

    def get_filtered_query_set(self):
        filtered_qs = self.filter_queryset(self.get_queryset())
        # Check object-level permissions for each entry.
        for entry in filtered_qs:
            self.check_object_permissions(self.request, entry)
        return filtered_qs

    def _visible(self, queryset):
        """Rows the caller may read; the others do not exist for it (LEX_SEC_NOT_FOUND)."""
        if not strict("NOT_FOUND"):
            return queryset
        return apply_read_restriction(queryset, self.request)

    def _invisible_target(self, queryset):
        """A 404 when the request names a row the caller may not read."""
        if not strict("NOT_FOUND"):
            return None
        if self._visible(queryset).count() != queryset.count():
            return raise_or_return(Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND))
        return None

    def _edit_denial(self, targets, data):
        """permission_edit for every row a bulk PATCH touches (LEX_SEC_EDIT)."""
        pk_name = self.kwargs['model_container'].pk_name
        by_pk = {}
        if isinstance(data, list):
            by_pk = {str(item.get(pk_name)): item for item in data if isinstance(item, dict)}
        for instance in targets:
            changes = by_pk.get(str(instance.pk), {}) if isinstance(data, list) else data
            denial = edit_denial(self.request, instance, changes, serializer=self.get_serializer(instance))
            if denial is not None:
                return denial
        return None

    def get(self, request, *args, **kwargs):
        queryset = self._visible(self.get_filtered_query_set())
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def patch(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
        queryset = self.get_filtered_query_set()
        missing = self._invisible_target(queryset)
        if missing is not None:
            return missing
        targets = list(queryset)
        denial = self._edit_denial(targets, request.data)
        if denial is not None:
            return raise_or_return(denial)
        serializer = self.get_serializer(queryset, data=request.data, partial=True, many=True)
        try:
            serializer.is_valid(raise_exception=True)
            # Perform bulk update and log each updated instance.
            self.perform_bulk_update(serializer)
        except Exception as exc:
            self._log_failed_bulk_request_audit_if_needed(
                action="update",
                targets=targets,
                payloads=getattr(serializer, "validated_data", None) or request.data,
                exception=exc,
            )
            raise
        model_class = self.kwargs['model_container'].model_class
        broadcast_model_mutation(model_class._meta.model_name, "updated", target=model_class)
        pk_name = self.kwargs['model_container'].pk_name
        return Response([d[pk_name] for d in serializer.data])

    def delete(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
        queryset = self.get_filtered_query_set()
        missing = self._invisible_target(queryset)
        if missing is not None:
            return missing
        targets = list(queryset)
        # permission_delete for every row, as for a single delete (LEX_SEC_EDIT).
        if strict("EDIT"):
            refused = [instance.pk for instance in targets if not delete_allowed(request, instance)]
            if refused:
                detail = "You may not delete some of these records."
                return raise_or_return(Response(
                    {"code": "DELETE_NOT_ALLOWED", "detail": detail, "error": detail, "ids": refused},
                    status=status.HTTP_400_BAD_REQUEST,
                ))
        payloads = [self.get_serializer(instance).data for instance in targets]
        try:
            # Perform bulk delete and log deletion of each instance.
            deleted_ids = self.perform_bulk_destroy(queryset)
        except Exception as exc:
            self._log_failed_bulk_request_audit_if_needed(
                action="delete",
                targets=targets,
                payloads=payloads,
                exception=exc,
            )
            raise
        model_class = self.kwargs['model_container'].model_class
        broadcast_model_mutation(model_class._meta.model_name, "deleted", target=model_class)
        return Response(deleted_ids)