from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

class ModelPermissions(APIView):
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model_container = self.kwargs['model_container']
        if hasattr(model_container, "get_permission_restrictions_for_request"):
            restrictions = model_container.get_permission_restrictions_for_request(request)
        else:
            restrictions = model_container.get_general_modification_restrictions_for_user(
                request.user
            )

        model_restrictions = {model_container.id: restrictions}

        return Response(model_restrictions)