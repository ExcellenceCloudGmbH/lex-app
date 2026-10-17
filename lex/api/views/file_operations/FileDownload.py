import os
from io import BytesIO

from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models
from django.http import FileResponse, JsonResponse
from django_sharepoint_storage.SharePointCloudStorageUtils import get_server_relative_path
from django_sharepoint_storage.SharePointContext import SharePointContext
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.security import is_readable, read_result
from lex.core.security_flags import strict


def _error(message, status_code):
    """An error of this view: raised to the exception handler (LEX_SEC_ERRORS) or the old JSON body."""
    from lex.api.errors import error_from_body, errors_raised

    if errors_raised():
        raise error_from_body({"error": message}, status_code)
    return JsonResponse({"error": message}, status=status_code)


class FileDownloadView(APIView):
    model_collection = None
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # 1. Fetch the model and instance
        model = kwargs['model_container'].model_class
        pk = request.query_params.get('pk')
        lookup = request.query_params.get('lookup')
        field_name = request.query_params.get('field')
        lookup_field = getattr(model, "lex_lookup_field", None)
        if not (pk or (lookup and lookup_field)) or not field_name:
            return _error("Both 'pk' (or 'lookup') and 'field' are required.", 400)
        if pk and lookup_field and getattr(model, "lex_lookup_allow_pk", True) is False:
            return _error("File not found", 404)

        # Only real file fields can be downloaded; any other attribute is "not found".
        try:
            field = model._meta.get_field(field_name)
        except FieldDoesNotExist:
            field = None
        if not isinstance(field, models.FileField):
            return _error("File not found", 404)

        # Using filter().first() is safer than [0] to avoid IndexError if not found
        try:
            criteria = {"pk": pk} if pk else {lookup_field: lookup}
            instance = model.objects.filter(**criteria).first()
        except (ValueError, TypeError, ValidationError):
            instance = None

        if not instance:
            return _error("File not found", 404)

        # The caller must be allowed to read the record and this field (LEX_SEC_READ_SURFACES).
        if strict("READ_SURFACES") and not self._may_read_file(request, instance, field_name):
            return _error("File not found", 404)

        # 2. Get the file field object safely
        file_obj = getattr(instance, field_name)
        if not file_obj:
            return _error("File not found", 404)

        storage_type = os.getenv("STORAGE_TYPE", "LOCAL")

        # 3. Handle Storage Types
        if storage_type == "SHAREPOINT":
            # SharePoint typically works with the URL/Relative path structure
            shrp_ctx = SharePointContext()

            # Ensure we pass the expected URL format to SharePoint utils
            # If your util expects a relative path without leading slash:
            rel_url = file_obj.url.lstrip('/')

            target_file = shrp_ctx.ctx.web.get_file_by_server_relative_path(
                get_server_relative_path(file_obj.url)
            ).execute_query()

            binary_file = target_file.open_binary(
                shrp_ctx.ctx,
                get_server_relative_path(rel_url)
            )
            bytesio_object = BytesIO(binary_file.content)
            return FileResponse(bytesio_object)

        elif storage_type == "GCS":
            # Cloud storage usually just needs the public/signed URL
            return JsonResponse({"download_url": file_obj.url})

        else:
            # LOCAL STORAGE
            # FIX: Use .path instead of .url for filesystem operations.
            # .path provides the absolute OS-specific path automatically.
            try:
                return FileResponse(open(file_obj.path, 'rb'))
            except NotImplementedError:
                # A field with its own (non-filesystem) storage has no .path.
                return FileResponse(file_obj.open('rb'))
            except FileNotFoundError:
                return _error("File not found on server", 404)

    @staticmethod
    def _may_read_file(request, instance, field_name):
        if not is_readable(request, instance):
            return False
        result = read_result(request, instance)
        if not result.allowed:
            return False
        all_fields = {f.name for f in instance._meta.fields}
        return field_name in set(result.get_fields(all_fields))