"""Read-only calculation state for one record.

Serves the Streamlit calculation widget, which polls this while a calculation
runs. Deliberately narrow: it returns only what the widget renders, so polling
stays cheap regardless of how wide the model is.

Reads are filtered by the record's own read permission, via the same
``UserReadRestrictionFilterBackend`` that guards list reads. A record the caller
may not read is reported exactly as a record that does not exist -- same status
code, same body -- because a distinguishable response would itself confirm the
record exists and leak its calculation state.

"""

from django.http import JsonResponse
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

class CalculationStatus(APIView):
    http_method_names = ["get"]
    permission_classes = [IsAuthenticated]

    def get(self, request, *args, **kwargs):
        model_container = self.kwargs["model_container"]
        model_class = model_container.model_class
        pk = self.kwargs["pk"]

        instance = self._readable_or_none(request, model_class, pk)
        if instance is None:
            # Deliberately identical to a genuine 404 -- same status AND same
            # body. Distinguishing "you may not read this" from "this does not
            # exist" would confirm the record exists to a caller who is not
            # allowed to see it, leaking its calculation state.
            return JsonResponse({"detail": "Not found."}, status=404)

        return JsonResponse(self._envelope(instance))

    def _readable_or_none(self, request, model_class, pk):
        """The record, or ``None`` when it is missing OR unreadable by this caller.

        Runs the single-row queryset through
        :class:`~lex.api.views.model_entries.filter_backends.UserReadRestrictionFilterBackend`
        -- the same backend ``ListModelEntries`` applies to every list read --
        rather than a hand-rolled check. Read permission in this codebase is a
        *queryset filter*, not a boolean: for models on the default
        ``LexModel.permission_read`` the backend translates Keycloak
        ``request.user_permissions`` into a DB filter, and only for models with
        a custom ``permission_read`` does it evaluate rows individually. Reusing
        the backend means this endpoint cannot drift from what a normal record
        fetch allows, and inherits every special case (legacy ``can_read``,
        AuditLog handling) for free.
        """
        queryset = model_class.objects.filter(pk=pk)
        readable = UserReadRestrictionFilterBackend().filter_queryset(
            request, queryset, self,
        )
        return readable.first()

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
