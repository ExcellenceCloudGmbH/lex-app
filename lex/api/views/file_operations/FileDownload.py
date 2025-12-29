import os
from io import BytesIO

from django.http import FileResponse, JsonResponse
from django_sharepoint_storage.SharePointCloudStorageUtils import (
    get_server_relative_path,
)
from django_sharepoint_storage.SharePointContext import SharePointContext
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey


class FileDownloadView(APIView):
    model_collection = None
    http_method_names = ["get"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model = kwargs["model_container"].model_class

        # Use get_object_or_404 for better error handling (404 instead of 500 crash)
        instance = get_object_or_404(model, pk=request.query_params["pk"])

        # SECURITY NOTE: Validate 'field' exists to prevent arbitrary attribute access
        field_name = request.query_params["field"]
        if not hasattr(instance, field_name):
            return Response({"error": "Field not found"}, status=400)

        file_obj = getattr(instance, field_name)

        # Get the raw URL from the storage backend
        # If the file doesn't exist, this might raise a ValueError depending on storage
        try:
            raw_url = file_obj.url
        except ValueError:
            return Response({"error": "File does not exist"}, status=404)

        # 1. WINDOWS FIX: Ensure URL uses forward slashes.
        # Windows paths might introduce '\', which breaks URLs.
        clean_url = raw_url.replace("\\", "/")

        if os.getenv("KUBERNETES_ENGINE", "NONE") == "NONE":
            # 2. LOCAL DEV FIX: Build absolute URI
            # If running locally, you often get a relative path (e.g., /media/file.jpg).
            # This converts it to http://localhost:8000/media/file.jpg
            file_url = request.build_absolute_uri(clean_url)
        else:
            # Production usually uses S3/GCS, returning a full https:// URL already.
            file_url = clean_url

        if os.getenv("STORAGE_TYPE") == "SHAREPOINT":
            shrp_ctx = SharePointContext()
            file = shrp_ctx.ctx.web.get_file_by_server_relative_path(
                get_server_relative_path(file.url)
            ).execute_query()
            binary_file = file.open_binary(
                shrp_ctx.ctx, get_server_relative_path(file_url)
            )
            bytesio_object = BytesIO(binary_file.content)
            return FileResponse(bytesio_object)
        elif os.getenv("STORAGE_TYPE") == "GCS":
            return JsonResponse({"download_url": file_url})
        else:
            return FileResponse(open(file_url, "rb"))
