"""
Shared models for Cluster 10 — API Layer.

Per rule #3 we keep dedicated models even though the shapes overlap
with Cluster 2 / 7. The cluster asserts the *HTTP* contract — the
observable REST-layer wiring on top of everything below.
"""

from __future__ import annotations

from django.db import models
from lex.core.mixins.ModelModificationRestriction import ModelModificationRestriction
from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("cluster 10")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("cluster 10")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class ApiSimpleItem(LexModel):
    """Plain LexModel for CRUD + history endpoint tests."""

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name



# ---------------------------------------------------------------------------
# Schema-introspection + search models (Cluster 10e + 10f)
# ---------------------------------------------------------------------------

@_permissive
class SchemaFKTarget(LexModel):
    """FK target used by :class:`SchemaItem` to exercise the
    ``foreign_key`` branch of ``create_field_info``."""

    label = models.CharField(max_length=64)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.label


@_permissive
class SchemaItem(LexModel):
    """One field per interesting Django type so 10.11 can table-drive the
    ``create_field_info`` type mapping. Also the primary subject of the
    10f search scenarios — the ``name`` char field is what
    ``SearchVector`` indexes against."""

    name = models.CharField(max_length=200)                     # string
    amount = models.IntegerField(default=0)                     # int
    ratio = models.FloatField(default=0.0)                      # float
    active = models.BooleanField(default=True)                  # boolean
    day = models.DateField(null=True, blank=True)               # date
    when = models.DateTimeField(null=True, blank=True)          # date_time
    payload = models.JSONField(default=dict, blank=True)        # json
    target = models.ForeignKey(                                 # foreign_key
        SchemaFKTarget,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name


class SchemaHiddenItem(LexModel):
    """``permission_list`` returns False — used by 10.14 to prove that
    ``delete_restricted_nodes_from_model_structure`` prunes denied
    model nodes AND collapses the folder that held them."""

    name = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def permission_read(self, uc):
        return PermissionResult.allow_all("schema hidden read-open")

    def permission_list(self, uc):
        return False  # hidden from nav

    def permission_edit(self, uc):
        return PermissionResult.allow_all("schema hidden edit-open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def __str__(self) -> str:  # pragma: no cover
        return self.name


@_permissive
class ApiLayerCalc(CalculationModel):
    """CalculationModel used to exercise API-layer calc control endpoints."""

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):  # pragma: no cover
        return None


class ApiLayerCalcReadRestricted(CalculationModel):
    """CalculationModel whose ``permission_read`` denies the ``deny_all`` group.

    Deliberately *not* ``@_permissive``: the read-leak scenario (10.76) needs a
    record that genuinely fails the record's own read permission for a real
    caller. The group-driven deny profile mirrors Cluster 4e's
    ``FilterBackendItem`` so the endpoint is exercised through the real
    permission pipeline (``permission_read`` →
    ``UserReadRestrictionFilterBackend``) instead of a monkeypatch that could
    diverge from what a normal record fetch allows.
    """

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):  # pragma: no cover
        return None

    def permission_read(self, uc):
        if "deny_all" in uc.groups:
            return PermissionResult.deny(
                "cluster 10o: deny-all profile — record must stay invisible",
            )
        return PermissionResult.allow_all("cluster 10o: readable by default")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 10o")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


#: Django group whose members may read :class:`ApiLayerCalcCalculateRestricted`
#: but may not run it. Used by 10.82; 10.83 is the same caller without it.
NO_CALCULATE_GROUP = "no_calculate"

#: The restriction's own explanation, appended to ``violations``. The framework
#: puts this text in the 403 body, so the status envelope may repeat it without
#: telling the caller anything pressing the button would not.
NO_CALCULATE_VIOLATION = "cluster 10o: this profile may read the record but not run it"


class RunDeniedForBlockedGroup(ModelModificationRestriction):
    """Denies *modification* — and therefore the calculate trigger — to one group.

    ``calculate=true`` is a PATCH through ``One.update``, which DRF authorises
    with ``UserPermission`` → the model's ``modification_restriction``. Denying
    here denies the trigger itself, so 10.82's envelope assertion is a statement
    about the permission the framework really enforces rather than about a
    parallel rule that could drift away from it.

    Read is left wide open on purpose: the pair of scenarios needs a caller who
    *may* see the record — otherwise the endpoint would 404 (10.76) and there
    would be no envelope to carry ``can_calculate`` at all.
    """

    def _is_blocked(self, user) -> bool:
        groups = getattr(user, "groups", None)
        if groups is None:
            return False
        return groups.filter(name=NO_CALCULATE_GROUP).exists()

    def can_modify_in_general(self, user, violations):
        if self._is_blocked(user):
            # ``violations`` is None on some framework call sites (see
            # ``ModelContainer.get_permission_restrictions_for_user``).
            if violations is not None:
                violations.append(NO_CALCULATE_VIOLATION)
            return False
        return True


@_permissive
class ApiLayerCalcCalculateRestricted(CalculationModel):
    """Readable by everyone, runnable only by callers outside the blocked group.

    The asymmetry is the point: read permission alone cannot answer "may this
    caller press Calculate", so an endpoint that inferred one from the other
    would be wrong for exactly this record.
    """

    name = models.CharField(max_length=200)
    calculation_error_message = models.TextField(blank=True, default="")

    modification_restriction = RunDeniedForBlockedGroup()

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):  # pragma: no cover
        return None


ALL_MODELS = [
    ApiSimpleItem,
    SchemaFKTarget,
    SchemaItem,
    SchemaHiddenItem,
    ApiLayerCalc,
    ApiLayerCalcReadRestricted,
    ApiLayerCalcCalculateRestricted,
]

API_SIMPLE = "apisimpleitem"
API_LAYER_CALC = "apilayercalc"
API_LAYER_CALC_RESTRICTED = "apilayercalcreadrestricted"
API_LAYER_CALC_RUN_RESTRICTED = "apilayercalccalculaterestricted"
