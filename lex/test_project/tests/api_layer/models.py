"""
Shared models for Cluster 10 — API Layer.

Per rule #3 we keep dedicated models even though the shapes overlap
with Cluster 2 / 7. The cluster asserts the *HTTP* contract — the
observable REST-layer wiring on top of everything below.
"""

from __future__ import annotations

from django.db import models
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


class _SchemaPreviewSerializer:
    """Stand-in 'preview' serializer registered by :class:`SchemaLabeledFKTarget`.
    ``_target_has_preview_serializer`` only checks for the ``'preview'`` key, so
    the value just needs to be a registered entry — this keeps the model honest
    without dragging in DRF serializer machinery for a pure-introspection test."""


@_permissive
class SchemaLabeledFKTarget(LexModel):
    """FK target that DECLARES ``lex_fk_label_field`` and a ``'preview'``
    serializer, so 10.70 can prove ``create_field_info`` surfaces both hints
    off a real FK field (not a mock)."""

    label = models.CharField(max_length=64)

    lex_fk_label_field = "label"
    api_serializers = {"preview": _SchemaPreviewSerializer}

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.label


@_permissive
class SchemaLabeledItem(LexModel):
    """Holds a FK to :class:`SchemaLabeledFKTarget` so the FK-hint branch of
    ``create_field_info`` can be exercised against a real Django field."""

    name = models.CharField(max_length=200)
    fund = models.ForeignKey(
        SchemaLabeledFKTarget,
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


ALL_MODELS = [
    ApiSimpleItem,
    SchemaFKTarget,
    SchemaItem,
    SchemaLabeledFKTarget,
    SchemaLabeledItem,
    SchemaHiddenItem,
    ApiLayerCalc,
]

API_SIMPLE = "apisimpleitem"
API_LAYER_CALC = "apilayercalc"
