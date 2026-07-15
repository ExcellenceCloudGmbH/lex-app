"""Scale signal for the on-demand recovery-beat pod.

The recovery-beat pod (embedded Celery beat that fires the dead-worker sweep)
used to run always-on. It only has work to do while calculations are in flight,
so it now runs **on demand**: KEDA polls this endpoint (a ``metrics-api``
trigger) and keeps the ``recovery-beat`` Deployment at one replica while the
returned ``count`` is greater than zero, scaling it back to zero when there is
nothing left to sweep.

The count is the number of calculation tasks the recovery machinery still needs
to watch for this instance, taken as the **union** of two independent signals so
a single one being briefly unavailable never scales the sweeper away from work
that still needs it:

* the recovery **registry** index (Redis, cross-process) — populated the moment
  a task is dispatched and cleared when it reaches a terminal state; this is
  exactly the set the sweep operates on, including dead-but-not-yet-recovered
  tasks whose heartbeat has expired;
* the in-process **active-calculation store** — reconciled against the database
  on read (terminal rows are pruned), so it still reports live work even during
  a transient Redis blip that would momentarily empty the registry read.

Fail-safe: any unexpected error reports a positive count, so the sweeper stays
up rather than scaling down and abandoning work it cannot currently see.
"""

from django.http import JsonResponse
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.views import APIView
from rest_framework_api_key.permissions import HasAPIKey

from lex.api.utils.api_key_requests import is_instance_api_key_request


class HasInstanceAPIKey(BasePermission):
    def has_permission(self, request, view):
        return is_instance_api_key_request(request)


def active_recovery_count() -> int:
    """Union of the two "work still in flight" signals (see module docstring)."""
    registry_count = 0
    store_count = 0
    try:
        from lex.lex_app.celery_recovery import registry

        registry_count = len(registry.list_tracked())
    except Exception:
        # list_tracked already degrades to [] internally; this guards the import.
        registry_count = 0
    try:
        from lex.core.signals.ActiveCalculationStateStore import (
            ActiveCalculationStateStore,
        )

        store_count = len(ActiveCalculationStateStore.snapshot())
    except Exception:
        store_count = 0
    return max(registry_count, store_count)


class RecoveryScaleMetric(APIView):
    """KEDA scale metric for the on-demand recovery-beat pod."""

    http_method_names = ["get"]
    permission_classes = [HasInstanceAPIKey | HasAPIKey | IsAuthenticated]

    def get(self, request, *args, **kwargs):
        try:
            count = active_recovery_count()
        except Exception:
            # Never let a scale-metric error scale the sweeper down onto work
            # it can't see — report a positive count and keep beat up.
            count = 1
        return JsonResponse({"count": count})
