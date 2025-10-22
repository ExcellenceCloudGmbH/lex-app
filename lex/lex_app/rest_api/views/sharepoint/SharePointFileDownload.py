from django.core.files.storage import default_storage
from django.http import FileResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey


class SharePointFileDownload(APIView):
    model_collection = None
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model = kwargs['model_container'].model_class
        instance = model.objects.filter(pk=request.query_params['pk'])[0]
        file = instance.__getattribute__(request.query_params['field'])

        return FileResponse(default_storage.open(file.name, "rb"))