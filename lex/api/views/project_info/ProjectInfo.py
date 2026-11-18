import os

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from lex.api.utils.api_key_requests import HasAPIKey


class ProjectInfo(APIView):
    """What the frontend needs to know about the project before it draws anything.

    Besides the project's name, branch and environment: whether Reflex
    dashboards are on (``reflex_enabled``), whether the project has a Reflex home
    page (``reflex_home``), and which framework draws each model's record and
    table dashboards (``dashboards`` -- see :mod:`lex.lex_app.dashboard_surfaces`).
    """

    permission_classes = [HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
            from lex.lex_app.dashboard_surfaces import (
                dashboard_surfaces,
                reflex_enabled,
                reflex_home_available,
            )

            result = {"project_name": os.getenv("LEX_SUBMODEL_NAME"),
                      "branch_name": os.getenv("LEX_SUBMODEL_BRANCH"),
                      "environment": os.getenv("DEPLOYMENT_ENVIRONMENT"),
                      "reflex_enabled": reflex_enabled(),
                      "reflex_home": reflex_home_available(),
                      "dashboards": dashboard_surfaces()}
            return Response(result)
