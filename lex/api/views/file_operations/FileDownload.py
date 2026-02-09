import os
from io import BytesIO
from django.http import FileResponse, JsonResponse
from django_sharepoint_storage.SharePointCloudStorageUtils import get_server_relative_path
from django_sharepoint_storage.SharePointContext import SharePointContext
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework_api_key.permissions import HasAPIKey


# Assuming HasAPIKey is imported from your custom permissions

class FileDownloadView(APIView):
    model_collection = None
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        # 1. Fetch the model and instance
        model = kwargs['model_container'].model_class
        # Using filter().first() is safer than [0] to avoid IndexError if not found
        instance = model.objects.filter(pk=request.query_params['pk']).first()

        if not instance:
            return JsonResponse({"error": "File not found"}, status=404)

        # 2. Get the file field object safely
        field_name = request.query_params['field']
        file_obj = getattr(instance, field_name)

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
            except FileNotFoundError:
                return JsonResponse({"error": "File not found on server"}, status=404)