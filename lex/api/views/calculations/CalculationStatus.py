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

``can_calculate`` reports whether *this* caller may trigger a run on this record,
so the widget can draw a button that tells the truth instead of one that only
fails when pressed. It decides nothing: the trigger is a PATCH through
``One.update``, which authorises itself exactly as it always has. This endpoint
merely runs the same permission class against the same payload and reports what
that authorisation would say -- see ``_calculate_permission``.

"""

import logging

from django.http import JsonResponse
from lex.api.views.model_entries.filter_backends import UserReadRestrictionFilterBackend
from lex.api.views.permissions.UserPermission import UserPermission
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

logger = logging.getLogger(__name__)

#: Most recent log lines returned when ``include_log`` is requested. Bounded so
#: a long calculation cannot turn a 2-second poll into a large response.
LOG_TAIL_LIMIT = 50

#: The body ``lex_calculation_streamlit()`` sends to start a run. The probe
#: carries it verbatim: a ``modification_restriction`` may inspect
#: ``request_data``, and answering for a payload other than the one the button
#: will actually send is one way the two endpoints could drift apart.
TRIGGER_PAYLOAD = {"calculate": "true"}

#: Rendered beside the disabled button when the framework offers no usable text
#: of its own.
DEFAULT_DENIED_REASON = "You do not have permission to run this calculation."


class _TriggerProbeRequest:
    """The caller's own request, presented as the PATCH the button would send.

    ``UserPermission`` branches on ``request.method`` and hands ``request.data``
    to the model's ``modification_restriction``; everything else it reads --
    ``user`` above all -- must stay the real request's, or the probe would be
    answering about somebody else. Hence delegation rather than a fabricated
    request object.
    """

    method = "PATCH"

    def __init__(self, request):
        # Assigned first: ``__getattr__`` below dereferences it.
        self._request = request
        self.data = dict(TRIGGER_PAYLOAD)

    def __getattr__(self, name):
        return getattr(self._request, name)


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

        envelope = self._envelope(request, instance)
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

    def _envelope(self, request, instance) -> dict:
        started, finished = self._run_window(instance)
        can_calculate, denied_reason = self._calculate_permission(request, instance)
        return {
            "status": instance.is_calculated,
            "error": self._error_of(instance),
            "started_at": started.isoformat() if started else None,
            "finished_at": finished.isoformat() if finished else None,
            "duration_seconds": (
                (finished - started).total_seconds() if started and finished else None
            ),
            "can_calculate": can_calculate,
            "calculate_denied_reason": denied_reason,
        }

    def _calculate_permission(self, request, instance):
        """``(can_calculate, reason)`` for this caller on this record.

        Answered by :class:`~lex.api.views.permissions.UserPermission` -- the
        very permission class ``OneModelEntry`` declares -- evaluated against the
        PATCH the widget's button would send. Reusing the class instead of
        re-deriving the rule is the whole point: a parallel check that says "no"
        to somebody ``One.update`` would have accepted disables a button with
        nothing left to press, and one that says "yes" to somebody it would
        refuse just restores the click-then-403 this replaced.

        This is emphatically *not* the authorisation. Nothing here decides
        whether a calculation may run; ``One.update`` still does, on its own
        request, unchanged. This only reports the decision that endpoint would
        reach, so the widget can draw an honest button -- and the widget keeps
        its 403 handler for the moment the two disagree because the permission
        changed between the poll and the click.

        Nothing is disclosed that pressing the button would not disclose: the
        caller already passed this record's read permission to get here, and the
        denial text is what the 403 body would carry.
        """
        permission = UserPermission()
        probe = _TriggerProbeRequest(request)
        try:
            if not permission.has_permission(probe, self):
                return False, self._denial_reason(permission)
            if not permission.has_object_permission(probe, self, instance):
                # ``has_object_permission`` sets a message too, but builds it
                # from the wrong arguments upstream -- the instance and the user
                # land where the access type and the unit belong -- so it reads
                # as "You do not have general <record>-access to the requested
                # <user>.". Beside a disabled button that is worse than saying
                # nothing specific. Correcting it would change the 403 body of
                # every modify endpoint, which is not this change's business.
                return False, DEFAULT_DENIED_REASON
        except Exception:
            # A restriction that raises is an application bug, and the framework
            # already falls open on one (see ``One.create``). Falling open is
            # also the only safe direction for this flag: an enabled button that
            # then fails is recoverable and already handled, a button disabled by
            # mistake is a dead end. The PATCH is unaffected either way -- it
            # would raise for itself, and answer for itself.
            logger.exception(
                "Could not evaluate calculate permission for %s(pk=%r); "
                "reporting the button as enabled and leaving the decision to "
                "the trigger itself",
                type(instance).__name__,
                instance.pk,
            )
            return True, None
        return True, None

    @staticmethod
    def _denial_reason(permission) -> str:
        """The framework's own explanation for a refusal, or a plain fallback.

        ``UserPermission`` composes this from the model's
        ``modification_restriction`` ``violations`` -- text the model author
        wrote to explain the refusal to a user, and text this caller would
        receive in the 403 body anyway. Repeating it up front is what turns a
        disabled button from a dead end into an answer.
        """
        message = (getattr(permission, "message", None) or "").strip()
        return message or DEFAULT_DENIED_REASON

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
