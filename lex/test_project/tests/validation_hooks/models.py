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
from django_lifecycle.conditions import (
    WhenFieldHasChanged,
    WhenFieldValueChangesTo,
    WhenFieldValueIs,
    WhenFieldValueWas,
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


@_permissive
class _ConditionalHooksBase(LexModel):
    """
    Abstract carrier of every conditional-hook form the lean snapshot must
    keep working: legacy ``when=`` / ``when_any=`` parameters and the modern
    ``condition=`` objects, including a chained condition.

    Each hook appends a distinct label to ``type(self).hook_log`` so a test can
    assert exactly which hooks fired for a given mutation. Concrete subclasses
    differ only in ``lex_lean_initial_state`` so lean-vs-full behaviour can be
    compared field-for-field on identical hook declarations.
    """

    # Concrete subclasses each define their own; declared here for the type
    # checker / clarity only.
    hook_log: list[str] = []

    name = models.CharField(max_length=200)
    status = models.CharField(max_length=50, default="draft")
    amount = models.IntegerField(default=0)
    a = models.IntegerField(default=0)
    b = models.IntegerField(default=0)
    note = models.CharField(max_length=200, default="")

    class Meta:
        abstract = True
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    # Legacy parameter form: single field.
    @hook(AFTER_UPDATE, when="status", has_changed=True)
    def _legacy_when_status(self):
        type(self).hook_log.append("legacy_when_status")

    # Legacy parameter form: any of several fields.
    @hook(AFTER_UPDATE, when_any=["a", "b"], has_changed=True)
    def _legacy_when_any_ab(self):
        type(self).hook_log.append("legacy_when_any_ab")

    # Modern condition objects.
    @hook(AFTER_UPDATE, condition=WhenFieldHasChanged("amount"))
    def _cond_amount_changed(self):
        type(self).hook_log.append("cond_amount_changed")

    @hook(AFTER_UPDATE, condition=WhenFieldValueWas("status", "draft"))
    def _cond_status_was_draft(self):
        type(self).hook_log.append("cond_status_was_draft")

    @hook(AFTER_UPDATE, condition=WhenFieldValueChangesTo("status", "paid"))
    def _cond_status_changes_to_paid(self):
        type(self).hook_log.append("cond_status_changes_to_paid")

    # Chained condition: amount changed AND status is now paid.
    @hook(
        AFTER_UPDATE,
        condition=WhenFieldHasChanged("amount") & WhenFieldValueIs("status", "paid"),
    )
    def _cond_amount_changed_and_paid(self):
        type(self).hook_log.append("cond_amount_changed_and_paid")


@_permissive
class LeanConditionalItem(_ConditionalHooksBase):
    """Lean snapshot ON — every hook above must still behave identically."""

    hook_log: list[str] = []

    lex_lean_initial_state = True

    class Meta:
        app_label = "lex_app"


@_permissive
class FullConditionalItem(_ConditionalHooksBase):
    """Lean snapshot OFF — the control for lean-vs-full parity assertions."""

    hook_log: list[str] = []

    lex_lean_initial_state = False

    class Meta:
        app_label = "lex_app"


@_permissive
class LeanExtraFieldItem(LexModel):
    """
    Lean snapshot ON with a field consulted *imperatively* (not via any hook
    decorator), declared through ``lex_initial_state_extra_fields`` so the
    escape hatch keeps ``self.has_changed('note')`` working.
    """

    hook_log: list[str] = []

    lex_lean_initial_state = True
    lex_initial_state_extra_fields = ("note",)

    name = models.CharField(max_length=200)
    note = models.CharField(max_length=200, default="")
    untracked = models.CharField(max_length=200, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    @hook(AFTER_UPDATE)
    def _maybe_log_note(self):
        # Imperative change-detection — field name is invisible to the static
        # decorator scan, so it relies on lex_initial_state_extra_fields.
        if self.has_changed("note"):
            type(self).hook_log.append("note_changed")


ALL_MODELS = [
    PreValidatedItem,
    PostValidatedItem,
    HookOrderItem,
    LeanConditionalItem,
    FullConditionalItem,
    LeanExtraFieldItem,
]
