"""
Shared models for Cluster 3 — Validation Hooks.

Defined once here and imported by sub-cluster tests so Django registers
each model class only once.
"""

from __future__ import annotations

from django.db import models
from django_lifecycle import (
    AFTER_CREATE,
    AFTER_SAVE,
    AFTER_UPDATE,
    BEFORE_CREATE,
    BEFORE_SAVE,
    BEFORE_UPDATE,
    hook,
)
from lex.core.models.LexModel import LexModel, PermissionResult


def _permissive(cls):
    """Attach wide-open permission methods; Cluster 4 covers authz proper."""

    def _read(self, uc):
        return PermissionResult.allow_all("cluster 3")

    def _edit(self, uc):
        return PermissionResult.allow_all("cluster 3")

    def _create(self, uc):
        return True

    def _delete(self, uc):
        return True

    cls.permission_read = _read
    cls.permission_edit = _edit
    cls.permission_create = _create
    cls.permission_delete = _delete
    return cls


@_permissive
class PreValidatedItem(LexModel):
    """
    Raises from :meth:`pre_validation` when ``name == 'FORBIDDEN'``.

    Any such save must be cancelled — no row, no history.
    """

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def pre_validation(self) -> None:
        if self.name == "FORBIDDEN":
            raise ValueError("pre_validation rejected the name")


@_permissive
class PostValidatedItem(LexModel):
    """
    Raises from :meth:`post_validation` when ``value`` is negative.

    The framework must roll back to the pre-save snapshot and re-raise.
    """

    name = models.CharField(max_length=200)
    value = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def post_validation(self) -> None:
        if self.value < 0:
            raise ValueError("post_validation rejected the value")


@_permissive
class HookOrderItem(LexModel):
    """
    Records the order in which lifecycle hooks fire by appending names
    to a class-level list.

    Tests are expected to clear the list before each save.
    """

    # Shared across all instances — tests reset it in setUp().
    hook_log: list[str] = []

    name = models.CharField(max_length=200)

    class Meta:
        app_label = "lex_app"

    @hook(BEFORE_CREATE)
    def _log_before_create(self):
        type(self).hook_log.append("BEFORE_CREATE")

    @hook(BEFORE_UPDATE)
    def _log_before_update(self):
        type(self).hook_log.append("BEFORE_UPDATE")

    @hook(BEFORE_SAVE)
    def _log_before_save(self):
        type(self).hook_log.append("BEFORE_SAVE")

    @hook(AFTER_SAVE)
    def _log_after_save(self):
        type(self).hook_log.append("AFTER_SAVE")

    @hook(AFTER_CREATE)
    def _log_after_create(self):
        type(self).hook_log.append("AFTER_CREATE")

    @hook(AFTER_UPDATE)
    def _log_after_update(self):
        type(self).hook_log.append("AFTER_UPDATE")


ALL_MODELS = [PreValidatedItem, PostValidatedItem, HookOrderItem]
