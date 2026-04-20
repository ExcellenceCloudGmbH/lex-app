"""
Shared models for Cluster 9 — Signals & WebSocket.

Rule #3: own copies — do not reuse Cluster 7's shapes. Cluster 9 is
about the *side effects* of a calculation: the ``ActiveCalculationStateStore``
entry, the ``WebSocketNotifier`` broadcast, and the cache-cleanup call.

E2ETestCase patches those five boundaries by default; Cluster-9
tests stop the relevant patch in ``setUp`` so the real call fires
and can be asserted on.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 9")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 9")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class SigAtomicCalc(CalculationModel):
    """Used for state-store / notifier / cache tests."""

    name = models.CharField(max_length=200)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"SigAtomicCalc({self.name!r}) failing on purpose")


ALL_MODELS = [SigAtomicCalc]

SIG_ATOMIC = "sigatomiccalc"

