from django.core.files.storage import default_storage
from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from lex.api.utils.api_key_requests import HasAPIKey

from lex.audit_logging.mixins.FileOperationAuditMixin import FileOperationAuditMixin
from lex.audit_logging.utils.file_operation_audit import ACTION_DOWNLOAD


class SharePointFileDownload(FileOperationAuditMixin, APIView):
    model_collection = None
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]
    audit_action = ACTION_DOWNLOAD

    def audit_request_payload(self, request, *args, **kwargs):
        return {
            "pk": request.query_params.get("pk"),
            "field": request.query_params.get("field"),
            "storage": "SHAREPOINT",
            "delivery": "stream",
        }

    def get(self, request, *args, **kwargs):
        model = kwargs['model_container'].model_class
        instance = model.objects.filter(pk=request.query_params['pk'])[0]
        file = instance.__getattribute__(request.query_params['field'])

        self.attach_audit(instance)
        self.record_audit(filename=file.name)

        return FileResponse(default_storage.open(file.name, "rb"))
