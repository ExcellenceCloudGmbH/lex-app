from django.http import JsonResponse

from django.views import View
class HealthCheck(View):
    authentication_classes = []

    def get(self, request):
        return JsonResponse({"status": "Healthy :)"})
