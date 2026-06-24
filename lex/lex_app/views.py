from django.http import JsonResponse

from django.views import View

from lex.lex_app.runtime_health import build_health_payload


class HealthCheck(View):
    authentication_classes = []

    def get(self, request):
        return JsonResponse(build_health_payload())
