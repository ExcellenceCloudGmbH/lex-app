from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

class ModelPermissions(APIView):
    http_method_names = ['get']
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model_container = self.kwargs['model_container']
        user = request.user

        model_restrictions = {model_container.id: model_container.get_general_modification_restrictions_for_user(user)}

        return Response(model_restrictions)