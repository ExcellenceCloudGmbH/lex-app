"""Read-only calculation state for one record.

Serves the Streamlit calculation widget, which polls this while a calculation
runs. Deliberately narrow: it returns only what the widget renders, so polling
stays cheap regardless of how wide the model is.

Reads are filtered by the record's own read permission, via the same
``UserReadRestrictionFilterBackend`` that guards list reads. A record the caller
may not read is reported exactly as a record that does not exist -- same status
code, same body -- because a distinguishable response would itself confirm the
record exists and leak its calculation state.

The log tail is opt-in via ``?include_log=true``: the widget's log panel is off
by default, so the ordinary poll must not read log rows at all. Run timings come
from ``CalculationLog`` as well, because the record itself is not stamped when a
calculation writes it. Both are scoped to the record's newest run -- see
``_latest_run_rows`` -- so the tail and the timings always describe the same one.

"""

from django.http import JsonResponse
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

#: Most recent log lines returned when ``include_log`` is requested. Bounded so
#: a long calculation cannot turn a 2-second poll into a large response.
LOG_TAIL_LIMIT = 50


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

        envelope = self._envelope(instance)
        # Opt-in only: the log keys stay absent -- not empty -- unless asked
        # for, so the poll a collapsed widget repeats every 2s never runs the
        # log query.
        if request.query_params.get("include_log") == "true":
            envelope.update(self._log_tail(instance))
        return JsonResponse(envelope)

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
        started, finished = self._run_window(instance)
        return {
            "status": instance.is_calculated,
            "error": self._error_of(instance),
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_seconds": (
                (finished - started).total_seconds() if started and finished else None
            ),
        }

    @staticmethod
    def _latest_run_rows(instance):
        """The record's ``CalculationLog`` rows, narrowed to its most recent run.

        ``CalculationLog`` points at records through a generic FK, so the rows
        are found by (content type, pk) rather than a reverse accessor.

        A record that has been calculated before still holds every earlier run's
        rows, so everything derived from the log has to say *which* run it is
        describing -- both the timings and the tail, or the envelope contradicts
        itself. The ``calculationId`` of the newest row is that run; it stays a
        ``Subquery`` so narrowing costs no extra round trip on a 2-second poll.
        """
        # Imported here rather than at module import: this view module is
        # pulled in while the URLconf is assembled.
        from django.contrib.contenttypes.models import ContentType
        from django.db.models import Subquery

        from lex.audit_logging.models.CalculationLog import CalculationLog

        content_type = ContentType.objects.get_for_model(type(instance))
        rows = CalculationLog.objects.filter(
            content_type=content_type, object_id=instance.pk,
        )
        latest_run = rows.order_by("-timestamp", "-id").values("calculationId")[:1]
        return rows.filter(calculationId=Subquery(latest_run))

    @classmethod
    def _run_window(cls, instance):
        """(start, end) of the record's most recent run, or ``(None, None)``.

        The record carries no timestamp of its own -- since PR #675 a
        calculation-owned save deliberately does not stamp ``edited_at`` -- so
        its ``CalculationLog`` rows are the only trace of when a run happened.

        Scoped to the newest run: a window spanning every run the record ever
        had would report days for a run that took seconds.
        """
        from django.db.models import Max, Min

        window = cls._latest_run_rows(instance).aggregate(
            first=Min("timestamp"), last=Max("timestamp"),
        )
        return window["first"], window["last"]

    @classmethod
    def _log_tail(cls, instance) -> dict:
        """The last ``LOG_TAIL_LIMIT`` lines of the newest run, oldest first.

        Scoped to the same run as ``_run_window``. Unscoped, a re-run that
        logged fewer than ``LOG_TAIL_LIMIT`` lines would have its tail padded
        out of the *previous* run's rows -- printing yesterday's lines beneath a
        header stating this run took seconds -- and would report the log as
        truncated whenever the record's whole history crossed the limit, however
        short the run being watched.

        One query, and one row more than the caller can receive: fetching
        ``LOG_TAIL_LIMIT + 1`` is what makes "there were earlier lines"
        answerable without a second COUNT over a table that grows with every
        line every calculation has ever logged.
        """
        newest_first = list(
            cls._latest_run_rows(instance)
            # id breaks ties: auto_now_add stamps can collide within a run, and
            # the tail must not reshuffle between two polls.
            .order_by("-timestamp", "-id")
            .values_list("calculation_log", flat=True)[: LOG_TAIL_LIMIT + 1]
        )

        truncated = len(newest_first) > LOG_TAIL_LIMIT
        lines = newest_first[:LOG_TAIL_LIMIT]
        lines.reverse()  # the widget prints a log top-down, oldest first
        return {"log": lines, "log_truncated": truncated}

    @staticmethod
    def _error_of(instance):
        """Read the subclass-convention error field, if the model has one."""
        for field in ("calculation_error_message", "error_message"):
            value = getattr(instance, field, None)
            if value:
                return value
        return None
