import logging
import traceback

from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
from lex.audit_logging.mixins.BulkAuditLogMixin import BulkAuditLogMixin
from lex.core.exceptions import resolve_exception_traceback
from lex.core.signals.ModelMutationSignal import broadcast_model_mutation
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

    def get(self, request, *args, **kwargs):
        queryset = self.get_filtered_query_set()
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def patch(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
        queryset = self.get_filtered_query_set()
        targets = list(queryset)
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
        broadcast_model_mutation(
            self.kwargs['model_container'].model_class._meta.model_name, "updated"
        )
        pk_name = self.kwargs['model_container'].pk_name
        return Response([d[pk_name] for d in serializer.data])

    def delete(self, request, *args, **kwargs):
        self.reset_failed_audit_log_state()
        queryset = self.get_filtered_query_set()
        targets = list(queryset)
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
        broadcast_model_mutation(
            self.kwargs['model_container'].model_class._meta.model_name, "deleted"
        )
        return Response(deleted_ids)