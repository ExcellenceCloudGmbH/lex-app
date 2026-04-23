"""
Shared models for Cluster 12 — Serializer Contract.

Each model exercises one slice of the JSON contract the
``LexSerializer`` layer is responsible for:

* :class:`RelatedItem` — tiny FK target used by ``WideItem`` to
  exercise FK round-trip (both read shape and PATCH-as-``{"id": X}``).
* :class:`WideItem` — one field per "interesting" type (Decimal,
  DateTime, Date, Time, UUID, JSON, choices, FK). Open permissions
  so read/write tests see every field.
* :class:`ProtectedWideItem` — same shape as ``WideItem`` but with a
  restrictive ``permission_read`` that returns
  ``PermissionResult.allow_fields({"name", "amount"})`` for
  non-admins. Proves field-level filtering survives the full
  ORM → serializer → JSON round-trip.

Rule #3: no cross-cluster imports — models live here and are
imported by the sub-cluster test modules only.
"""

from __future__ import annotations

import uuid

from django.db import models

from lex.core.models.LexModel import LexModel, PermissionResult


# ---------------------------------------------------------------------
# FK target
# ---------------------------------------------------------------------
class RelatedItem(LexModel):
    """Small FK target. Minimal surface — tests only need ``id`` and ``name``."""

    name = models.CharField(max_length=64)
    code = models.CharField(max_length=16, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 12: fk target")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 12: fk target")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# Wide row — one field per interesting type
# ---------------------------------------------------------------------
CHOICE_ALPHA = "alpha"
CHOICE_BETA = "beta"
CHOICE_GAMMA = "gamma"
WIDE_CHOICES = [
    (CHOICE_ALPHA, "Alpha"),
    (CHOICE_BETA, "Beta"),
    (CHOICE_GAMMA, "Gamma"),
]


class WideItem(LexModel):
    """One field per type. Permissions wide-open so the shape is observable."""

    name = models.CharField(max_length=200)
    # Numeric
    amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    count = models.IntegerField(default=0)
    is_flagged = models.BooleanField(default=False)
    # Temporal
    created_on = models.DateField(null=True, blank=True)
    created_at_ts = models.DateTimeField(null=True, blank=True)
    daily_at = models.TimeField(null=True, blank=True)
    # Identifiers & text
    public_id = models.UUIDField(default=uuid.uuid4, editable=False)
    notes = models.TextField(blank=True, default="")
    # Enum-ish
    category = models.CharField(
        max_length=16, choices=WIDE_CHOICES, default=CHOICE_ALPHA,
    )
    # Structured
    payload = models.JSONField(default=dict, blank=True)
    # Relational
    related = models.ForeignKey(
        RelatedItem,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="wide_items",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:
        # Deliberately non-trivial so scenario 12.2 can assert the
        # serializer calls through to the model's ``__str__``.
        return f"Wide<{self.name}:{self.category}>"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 12: wide-open read")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 12: wide-open edit")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# Field-level-restricted wide row
# ---------------------------------------------------------------------
class ProtectedWideItem(LexModel):
    """``WideItem`` shape with restrictive per-field ``permission_read``.

    Admins see everything. Non-admins see ``{"id", "name", "amount"}``
    only — every other field must be absent from the serialized JSON.
    Pairs with 12.4 / 12.8 to prove field filtering round-trips.
    """

    name = models.CharField(max_length=200)
    amount = models.DecimalField(max_digits=12, decimal_places=4, default=0)
    secret_note = models.TextField(blank=True, default="")
    secret_category = models.CharField(
        max_length=16, choices=WIDE_CHOICES, default=CHOICE_ALPHA,
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_read(self, uc):
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin — all fields")
        return PermissionResult.allow_fields(
            {"id", "name", "amount"},
            "non-admins — name + amount only",
        )

    def permission_edit(self, uc):
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin — all fields")
        return PermissionResult.allow_fields(
            {"name"}, "non-admins — name only",
        )

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# M2M / FK write path fixtures (12f)
# ---------------------------------------------------------------------
class TagItem(LexModel):
    """Tiny tag used as both an M2M target and an FK target on
    :class:`TaggableItem`. Minimal surface — tests only assert on
    ``label``."""

    label = models.CharField(max_length=64, unique=True)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.label

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 12: tag open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 12: tag open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class TaggableItem(LexModel):
    """Row carrying an M2M (``tags``) and a nullable FK
    (``primary_tag``) for the Cluster-12f write-path scenarios."""

    title = models.CharField(max_length=200)
    tags = models.ManyToManyField(
        TagItem, blank=True, related_name="taggables",
    )
    primary_tag = models.ForeignKey(
        TagItem,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name="primary_taggables",
    )

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.title

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 12: taggable open")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 12: taggable open")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# Field-level edit scope fixture (12.8)
# ---------------------------------------------------------------------
class EditScopedItem(LexModel):
    """``permission_edit`` returns ``allow_fields({"x"})`` for non-admins.

    Used by scenario 12.8 to assert that ``lex_reserved_scopes.edit``
    on the serialized instance contains **exactly** those field names
    — no more, no less.
    """

    x = models.CharField(max_length=200, default="")
    y = models.CharField(max_length=200, default="")
    z = models.CharField(max_length=200, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"EditScoped<{self.pk}>"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 12: edit-scoped read open")

    def permission_edit(self, uc):
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin — all fields editable")
        return PermissionResult.allow_fields(
            {"x"}, "non-admins may only edit 'x'",
        )

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [
    RelatedItem,
    WideItem,
    ProtectedWideItem,
    TagItem,
    TaggableItem,
    EditScopedItem,
]

# URL names expected by ``process_admin_rest_api`` — lowercased model name.
RELATED = "relateditem"
WIDE = "wideitem"
PROTECTED_WIDE = "protectedwideitem"
TAG = "tagitem"
TAGGABLE = "taggableitem"
EDIT_SCOPED = "editscopeditem"

