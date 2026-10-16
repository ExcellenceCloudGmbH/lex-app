"""
Shared models for Cluster 2 — CRUD via REST API.

Defined once here and imported by the sub-cluster test modules so that
Django only registers each model class once.

Permissions are intentionally wide-open — Cluster 2 is about CRUD
mechanics, not access control (covered in Cluster 4).
"""

from __future__ import annotations

import uuid as _uuid

from django.db import models
from lex.core.models.LexModel import LexModel, PermissionResult


class SimpleItem(LexModel):
    """Plain LexModel with a few fields — the workhorse for CRUD tests."""

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)
    description = models.TextField(blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover — trivial
        return self.name

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 2: crud")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 2: crud")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class TrackedItem(LexModel):
    """Model used to assert ``created_by`` / ``edited_by`` actor resolution."""

    label = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.label

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 2: crud")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 2: crud")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


class PublicIdItem(LexModel):
    """Cluster 2m: addressed by a public uuid (lex_lookup_field)."""

    uuid = models.UUIDField(default=_uuid.uuid4, unique=True, editable=False)
    name = models.CharField(max_length=200)
    lex_lookup_field = "uuid"

    class Meta:
        app_label = "lex_app"

    def permission_read(self, uc):
        return PermissionResult.allow_all("cluster 2m")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("cluster 2m")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [SimpleItem, TrackedItem]
PUBLIC_ID_MODELS = [PublicIdItem]
PUBLIC_ID = "publiciditem"

# URL names expected by ``process_admin_rest_api`` — lowercased model name.
SIMPLE = "simpleitem"
TRACKED = "trackeditem"
