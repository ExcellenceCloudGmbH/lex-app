import os

from django.http import JsonResponse
from django_sharepoint_storage.SharePointCloudStorageUtils import get_server_relative_path
from django_sharepoint_storage.SharePointContext import SharePointContext
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from lex.api.utils.api_key_requests import HasAPIKey

from lex.audit_logging.mixins.FileOperationAuditMixin import FileOperationAuditMixin
from lex.audit_logging.utils.file_operation_audit import ACTION_DOWNLOAD


class SharePointPreview(FileOperationAuditMixin, APIView):
    model_collection = None
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]
    # A preview link exposes the file's contents just as a download does, so
    # it is audited the same way — the payload's `delivery` says which form.
    audit_action = ACTION_DOWNLOAD

    def audit_request_payload(self, request, *args, **kwargs):
        return {
            "pk": request.query_params.get("pk"),
            "field": request.query_params.get("field"),
            "storage": "SHAREPOINT",
            "delivery": "preview_link",
        }

    def get(self, request, *args, **kwargs):
        model = kwargs['model_container'].model_class
        shrp_ctx = SharePointContext()
        instance = model.objects.filter(pk=request.query_params['pk'])[0]
        file = instance.__getattribute__(request.query_params['field'])

        self.attach_audit(instance)
        self.record_audit(filename=file.name)

        file = shrp_ctx.ctx.web.get_file_by_server_relative_path(get_server_relative_path(file.url)).get().execute_query()
        preview_link = str(os.getenv('FILE_PREVIEW_LINK_BASE')) + "sourcedoc={" + file.unique_id + "}&action=embedview"

        self.record_audit(sharepoint_unique_id=file.unique_id)

        return JsonResponse({"preview_link": preview_link})
