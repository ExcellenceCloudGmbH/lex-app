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


class ApiProjectItem(LexModel):
    """Row-level read rule for the project-API tests: "hidden" rows are unreadable."""

    name = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def permission_read(self, user_context):
        if self.name == "hidden":
            return PermissionResult.deny("cluster 10o: hidden row")
        return PermissionResult.allow_all("cluster 10o")

    def permission_edit(self, user_context):
        return PermissionResult.allow_all("cluster 10o")

    def permission_create(self, user_context):
        return True

    def permission_delete(self, user_context):
        return True

    def permission_list(self, user_context):
        return True

    def save(self, *args, **kwargs):
        if self.name == "explode":
            raise RuntimeError("cluster 10p: save failed on purpose")
        return super().save(*args, **kwargs)

    def __str__(self) -> str:  # pragma: no cover
        return self.name


from django.http import HttpResponse  # noqa: E402
from rest_framework import serializers as _drf  # noqa: E402

from lex.api.errors import LexActionError  # noqa: E402
from lex.api.project import lex_action  # noqa: E402


class _ApproveInput(_drf.Serializer):
    note = _drf.CharField()


class _BatchInput(_drf.Serializer):
    count = _drf.IntegerField(min_value=1, max_value=5)


class ActionItem(LexModel):
    """Cluster 10s: actions beyond CRUD. "locked" rows are readable but not editable."""

    name = models.CharField(max_length=200)
    status = models.CharField(max_length=20, default="draft")
    note = models.CharField(max_length=200, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_read(self, uc):
        if self.name == "hidden":
            return PermissionResult.deny("cluster 10s: hidden")
        return PermissionResult.allow_all("cluster 10s")

    def permission_edit(self, uc):
        if self.status == "locked":
            return PermissionResult.deny("cluster 10s: locked")
        return PermissionResult.allow_all("cluster 10s")

    def permission_create(self, uc):
        return True

    def permission_list(self, uc):
        return True

    @classmethod
    def permission_download(cls, uc):
        return uc.is_superuser

    @lex_action(permission="edit", input=_ApproveInput)
    def approve(self, context, data):
        self.status = "approved"
        self.note = data["note"]
        self.save()
        return {"status": self.status, "by": getattr(context.user, "username", None)}

    @lex_action(permission="edit")
    def explode(self, context, data):
        ActionItem.objects.create(name="side-effect")
        raise LexActionError("Cannot do that now.", code="NOT_NOW", status_code=409)

    @lex_action(methods=("get",), permission="permission_download")
    def download(self, context, data):
        response = HttpResponse(f"name\n{self.name}\n", content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="{self.pk}.csv"'
        return response

    @lex_action()
    def unguarded(self, context, data):  # no permission declared: always refused
        return {"ran": True}

    @lex_action(detail=False, permission="create", input=_BatchInput)
    def batch(cls, context, data):
        created = [ActionItem.objects.create(name=f"batch-{i}") for i in range(data["count"])]
        return {"ids": [item.pk for item in created]}


ALL_MODELS = [ApiSimpleItem, SchemaFKTarget, SchemaItem, SchemaHiddenItem, ApiLayerCalc]
PROJECT_MODELS = [ApiProjectItem]
ACTION_MODELS = [ActionItem]
ACTION_ITEM = "actionitem"
API_PROJECT_ITEM = "apiprojectitem"

API_SIMPLE = "apisimpleitem"
API_LAYER_CALC = "apilayercalc"
