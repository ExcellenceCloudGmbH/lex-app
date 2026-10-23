from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey


class Widgets(APIView):
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, *args, **kwargs):
        from lex.model_admin.settings import lexAdminSite

        return JsonResponse({"widget_structure": lexAdminSite.widget_structure})
