import os

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from lex.api.utils.api_key_requests import HasAPIKey


class ProjectInfo(APIView):
    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
            result = {"project_name": os.getenv("LEX_SUBMODEL_NAME"),
                      "branch_name": os.getenv("LEX_SUBMODEL_BRANCH"),
                      "environment": os.getenv("DEPLOYMENT_ENVIRONMENT")}
            return Response(result)