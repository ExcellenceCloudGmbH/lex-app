from rest_framework.generics import GenericAPIView
from rest_framework.response import Response
from lex.api.views.model_entries.filter_backends import PrimaryKeyListFilterBackend
from lex.api.views.model_entries.mixins.ModelEntryProviderMixin import ModelEntryProviderMixin
from lex.audit_logging.mixins.bulk_audit_mixin import BulkAuditLogMixin
from django.apps import apps

class ManyModelEntries(BulkAuditLogMixin, ModelEntryProviderMixin, GenericAPIView):
    filter_backends = [PrimaryKeyListFilterBackend]

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
        queryset = self.get_filtered_query_set()
        serializer = self.get_serializer(queryset, data=request.data, partial=True, many=True)
        serializer.is_valid(raise_exception=True)
        # Perform bulk update and log each updated instance.
        self.perform_bulk_update(serializer)
        pk_name = self.kwargs['model_container'].pk_name
        return Response([d[pk_name] for d in serializer.data])

    def delete(self, request, *args, **kwargs):
        queryset = self.get_filtered_query_set()
        # Perform bulk delete and log deletion of each instance.
        deleted_ids = self.perform_bulk_destroy(queryset)
        return Response(deleted_ids)