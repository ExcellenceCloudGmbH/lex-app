"""Read-only calculation state for one record.

Serves the Streamlit calculation widget, which polls this while a calculation
runs. Deliberately narrow: it returns only what the widget renders, so polling
stays cheap regardless of how wide the model is.

"""

from django.http import JsonResponse
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

class CalculationStatus(APIView):
    http_method_names = ["get"]
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        model_class = model_container.model_class
        pk = self.kwargs["pk"]

        instance = model_class.objects.filter(pk=pk).first()
        if instance is None:
            return JsonResponse({"detail": "Not found."}, status=404)

        return JsonResponse(self._envelope(instance))

    def _envelope(self, instance) -> dict:
        return {
            "status": instance.is_calculated,
            "error": self._error_of(instance),
            "started_at": None,
            "finished_at": None,
            "duration_seconds": None,
        }

    @staticmethod
    def _error_of(instance):
        """Read the subclass-convention error field, if the model has one."""
        for field in ("calculation_error_message", "error_message"):
            value = getattr(instance, field, None)
            if value:
                return value
        return None
