"""
Shared models for ``test_project/tests/journeys/``.

End-to-end journey tests exercise multiple clusters at once. Rule #3
still applies — ``journeys/`` owns its own model file. The shapes are
deliberately small so the journey scripts remain readable.
"""

from __future__ import annotations

from django.db import models

from lex.core.models.CalculationModel import CalculationModel
from lex.core.models.LexModel import LexModel, PermissionResult


# ---------------------------------------------------------------------
# Journey A — Invoice (CRUD + authz + history + actor tracking)
# ---------------------------------------------------------------------
class Invoice(LexModel):
    """
    Simple finance record. Admins may create / update / delete;
    non-admins may read and edit the ``note`` field only.
    """

    customer = models.CharField(max_length=200)
    amount = models.IntegerField(default=0)
    note = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"Invoice({self.customer}:{self.amount})"

    def permission_read(self, uc):
        return PermissionResult.allow_all("journey-A read-open")

    def permission_edit(self, uc):
        if uc.is_superuser or "admin" in uc.groups:
            return PermissionResult.allow_all("admin may edit all fields")
        return PermissionResult.allow_fields(
            {"note"}, "non-admins may only edit the note",
        )

    def permission_create(self, uc):
        return uc.is_superuser or "admin" in uc.groups

    def permission_delete(self, uc):
        return uc.is_superuser or "admin" in uc.groups

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# Journey B — Portfolio + Position (parent/child calculations)
# ---------------------------------------------------------------------
def _permissive(cls):
    cls.permission_read = lambda self, uc: PermissionResult.allow_all("journey-B")
    cls.permission_edit = lambda self, uc: PermissionResult.allow_all("journey-B")
    cls.permission_create = lambda self, uc: True
    cls.permission_delete = lambda self, uc: True
    cls.permission_list = lambda self, uc: True
    return cls


@_permissive
class JourneyPosition(CalculationModel):
    """Leaf calculation — computes market value."""

    symbol = models.CharField(max_length=50)
    quantity = models.IntegerField(default=0)
    price = models.IntegerField(default=0)
    market_value = models.IntegerField(default=0)
    should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.symbol

    def calculate(self):
        if self.should_fail:
            raise RuntimeError(f"JourneyPosition({self.symbol!r}) failing on purpose")
        self.market_value = self.quantity * self.price


@_permissive
class JourneyPortfolio(CalculationModel):
    """
    Parent calculation — spawns a :class:`JourneyPosition` and totals
    its market value.
    """

    name = models.CharField(max_length=200)
    symbol = models.CharField(max_length=50, default="ACME")
    quantity = models.IntegerField(default=0)
    price = models.IntegerField(default=0)
    total_market_value = models.IntegerField(default=0)
    child_should_fail = models.BooleanField(default=False)
    calculation_error_message = models.TextField(blank=True, default="")

    is_atomic = True

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def calculate(self):
        position = JourneyPosition(
            symbol=self.symbol,
            quantity=self.quantity,
            price=self.price,
            should_fail=self.child_should_fail,
        )
        position.is_calculated = CalculationModel.IN_PROGRESS
        position.save()

        if position.is_calculated == CalculationModel.ERROR:
            raise RuntimeError(
                f"JourneyPortfolio({self.name!r}) — child failed",
            )

        self.total_market_value = position.market_value


# ---------------------------------------------------------------------
# Journey C — Employee record (role-aware field visibility)
# ---------------------------------------------------------------------
class Employee(LexModel):
    """
    Employee record used to exercise field-level visibility end-to-end.

    Visibility:
      * superuser  → everything
      * ``hr``     → everything except ``ssn``
      * regular    → ``id`` + ``name`` only
    """

    name = models.CharField(max_length=200)
    salary = models.IntegerField(default=0)
    ssn = models.CharField(max_length=32, blank=True, default="")

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return self.name

    def permission_read(self, uc):
        if uc.is_superuser:
            return PermissionResult.allow_all("superuser sees everything")
        if "hr" in uc.groups:
            return PermissionResult.allow_all_except({"ssn"}, "hr hides ssn only")
        return PermissionResult.allow_fields(
            {"id", "name"}, "regular staff see id + name",
        )

    def permission_edit(self, uc):
        if uc.is_superuser or "hr" in uc.groups:
            return PermissionResult.allow_all("hr/admin may edit")
        return PermissionResult.deny("regular staff may not edit")

    def permission_create(self, uc):
        return uc.is_superuser or "hr" in uc.groups

    def permission_delete(self, uc):
        return uc.is_superuser

    def permission_list(self, uc):
        return True


# ---------------------------------------------------------------------
# Journey E — ValidatedInvoice (validation-hook narrative)
# ---------------------------------------------------------------------
class ValidatedInvoice(LexModel):
    """
    Finance record guarded by both validation hooks.

    * ``pre_validation`` rejects ``customer == 'BLOCKED'`` — the save
      is cancelled before it hits the DB, so no row and no history
      row are ever produced.
    * ``post_validation`` rejects negative ``amount`` — the save has
      already landed in the DB, so the framework must roll back to
      the pre-save snapshot and re-raise.

    Permissions are wide-open: Journey E is about validation, not
    authz.
    """

    customer = models.CharField(max_length=200)
    amount = models.IntegerField(default=0)

    class Meta:
        app_label = "lex_app"

    def __str__(self) -> str:  # pragma: no cover
        return f"ValidatedInvoice({self.customer}:{self.amount})"

    def pre_validation(self) -> None:
        if self.customer == "BLOCKED":
            raise ValueError("pre_validation rejected this customer")

    def post_validation(self) -> None:
        if self.amount < 0:
            raise ValueError("post_validation rejected this amount")

    def permission_read(self, uc):
        return PermissionResult.allow_all("journey-E: validation only")

    def permission_edit(self, uc):
        return PermissionResult.allow_all("journey-E: validation only")

    def permission_create(self, uc):
        return True

    def permission_delete(self, uc):
        return True

    def permission_list(self, uc):
        return True


ALL_MODELS = [
    Invoice,
    JourneyPosition, JourneyPortfolio,
    Employee,
    ValidatedInvoice,
]

INVOICE = "invoice"
POSITION = "journeyposition"
PORTFOLIO = "journeyportfolio"
EMPLOYEE = "employee"
VALIDATED_INVOICE = "validatedinvoice"

