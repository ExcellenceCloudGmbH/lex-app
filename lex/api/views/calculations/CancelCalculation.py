"""User-initiated cancellation of a running calculation.

Endpoint
--------
``POST /api/cancel-calculation/<model_name>/<pk>``

The frontend's "Cancel" / "Abort" button calls this endpoint while a
``CalculationModel`` row is ``IN_PROGRESS``.  The view flags the running
calculation for cooperative cancellation via
:meth:`CalculationModel.request_cancel`; the actual transition to
``ABORTED`` happens inside the calculation (see
``execute_calculation_sync``) and the existing
``calculation_aborted`` WebSocket broadcast tells every connected client
to clear the spinner.

Sync route only (this iteration).  The Celery route is documented as a
follow-up — it needs the task id stored in the cache store, and a
``revoke(terminate=True)`` call on top of the cooperative flag.
"""

import logging

from django.apps import apps
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.core.models.CalculationModel import CalculationModel
from lex.lex_app.settings import repo_name

logger = logging.getLogger(__name__)


class CancelCalculation(APIView):
    """``POST`` an active calculation into the ``ABORTED`` state.

    Returns:

    * **202 Accepted** — cancel was registered against an active calculation.
      The ``ABORTED`` terminal state will be persisted asynchronously when
      the running ``calculate()`` next polls ``check_cancelled()`` or when
      it returns.
    * **200 OK** with ``{"status": "not_running"}`` — the record is not
      currently calculating.  Idempotent no-op so a double-click on the
      Cancel button is harmless.
    * **404 Not Found** — the record (or its model) does not exist.
    * **400 Bad Request** — the resolved model is not a ``CalculationModel``.
    """

    http_method_names = ["post"]
    permission_classes = [HasAPIKey | IsAuthenticated]

    def post(self, request, model_name: str, pk: str, *args, **kwargs):
        model_class = self._resolve_model(model_name)
        if model_class is None:
            return Response(
                {"error": f"Unknown model '{model_name}'."},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not issubclass(model_class, CalculationModel):
            return Response(
                {
                    "error": (
                        f"Model '{model_name}' is not a CalculationModel — "
                        "cancellation is only meaningful for calculation records."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        instance = model_class.objects.filter(pk=pk).first()
        if instance is None:
            return Response(
                {"error": f"{model_name}({pk}) not found."},
                status=status.HTTP_404_NOT_FOUND,
            )

        record_id = f"{model_class._meta.model_name}_{instance.pk}"
        requested_by = self._resolve_requested_by(request)

        cancel_registered = model_class.request_cancel(
            instance,
            requested_by=requested_by,
        )

        if not cancel_registered:
            # No active entry in the state store — either the calc never
            # started, already finished, or this server process did not
            # register it (e.g. another worker is running it).  Surface
            # this as a benign no-op so the UI's double-click guard
            # doesn't surface a scary error.
            logger.info(
                "Cancel request for %s ignored — no active calculation in this process.",
                record_id,
            )
            return Response(
                {
                    "status": "not_running",
                    "record_id": record_id,
                },
                status=status.HTTP_200_OK,
            )

        logger.info(
            "Cancel requested for %s by %s",
            record_id,
            requested_by or "<unknown>",
        )
        return Response(
            {
                "status": "cancel_requested",
                "record_id": record_id,
                "requested_by": requested_by,
            },
            status=status.HTTP_202_ACCEPTED,
        )

    @staticmethod
    def _resolve_model(model_name: str):
        """Resolve ``model_name`` to a model class.

        Tries the configured ``repo_name`` app first (the production
        path — matches sibling endpoints like ``CleanCalculations``);
        falls back to a search across every registered model so that
        tests and downstream apps with non-default app labels can still
        cancel calculations without re-configuring settings.
        """
        try:
            return apps.get_model(repo_name, model_name)
        except (LookupError, ValueError):
            pass

        normalized = model_name.lower()
        for candidate in apps.get_models():
            meta = getattr(candidate, "_meta", None)
            if meta is None:
                continue
            if meta.model_name == normalized:
                return candidate
        return None

    @staticmethod
    def _resolve_requested_by(request):
        """Resolve the actor that issued the cancel.

        Prefers ``request.user.username`` (the stable identifier the
        framework uses for audit ``created_by`` / ``edited_by``), then
        ``request.user.email``, then ``str(request.user)``.  Returns
        ``None`` when the request is anonymous so the response does not
        carry a misleading actor.
        """
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None
        for attr in ("username", "email"):
            value = getattr(user, attr, None)
            if isinstance(value, str) and value.strip():
                return value
        rendered = str(user).strip()
        return rendered or None
