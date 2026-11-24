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



# ---------------------------------------------------------------------------
# Calculation sockets follow the record's read permission (Cluster 9k)
# ---------------------------------------------------------------------------

#: The Keycloak client role that may read secret rows of :class:`SigGuardedCalc`.
GUARDED_CALC_AUDITOR_ROLE = "calc-auditor"


class SigGuardedCalc(CalculationModel):
    """Cluster 9k: a calculation model whose secret rows only an auditor may read.

    Open rows are readable by every signed-in user. A secret row's ``permission_read``
    refuses everyone but a user holding the Keycloak client role
    :data:`GUARDED_CALC_AUDITOR_ROLE` -- so a test can tell whether a socket is judged with
    its sign-in's roles, as an API call is.
    """

    name = models.CharField(max_length=200)
    is_secret = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):  # pragma: no cover
        return None

    def permission_read(self, uc):
        if self.is_secret and GUARDED_CALC_AUDITOR_ROLE not in (uc.client_roles or ()):
            return PermissionResult.deny("cluster 9k: secret row")
        return PermissionResult.allow_all("cluster 9k: readable row")

    def permission_list(self, uc):
        return True

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 9k: edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True


class SigScopedCalc(CalculationModel):
    """Cluster 9k: a calculation model on LexModel's default ``permission_read``.

    It does not override ``permission_read``, so a row is readable with the Keycloak ``read``
    scope on ``lex_app.SigScopedCalc`` (UMA) and refused without it -- the case of every model
    a project leaves to Keycloak.
    """

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):  # pragma: no cover
        return None


SOCKET_READ_MODELS = [SigGuardedCalc, SigScopedCalc]
