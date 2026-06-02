from django.db import models
from lex.core.models.LexModel import LexModel, UserContext, PermissionResult

from Input.Employee import Employee


class Expense(LexModel):
    """An individual expense submission with receipt upload."""

    employee = models.ForeignKey(Employee, on_delete=models.CASCADE)
    description = models.CharField(max_length=255)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    category = models.CharField(
        max_length=50,
        choices=[
            ("travel", "Travel"),
            ("software", "Software"),
            ("equipment", "Equipment"),
            ("meals", "Meals & Entertainment"),
            ("office", "Office Supplies"),
            ("other", "Other"),
        ],
    )
    date = models.DateField(help_text="Date the expense was incurred")
    quarter = models.CharField(
        max_length=10,
        help_text="e.g. Q1 2026",
    )
    receipt = models.FileField(
        upload_to="receipts/",
        null=True,
        blank=True,
        help_text="Upload a photo or PDF of the receipt",
    )

    def __str__(self):
        return f"{self.description} — €{self.amount}"

    # ── Validation ──

    def pre_validation(self):
        """Block invalid expenses before they are saved."""
        if self.amount <= 0:
            raise ValueError("Expense amount must be positive.")

        if self.amount > 10000:
            raise ValueError(
                "Expenses over €10,000 require manual approval. "
                "Please contact the CFO."
            )

    # ── Permissions ──

    def permission_read(self, user_context: UserContext) -> PermissionResult:
        """
        - Employees see only their own expenses
        - Managers see their team's expenses
        - CFO sees everything
        """
        if user_context.is_superuser:
            return PermissionResult.allow_all()

        # CFO sees everything
        if "cfo" in user_context.groups:
            return PermissionResult.allow_all()

        # Managers see their team's expenses
        if "manager" in user_context.groups:
            if self.employee.team.manager_email == user_context.email:
                return PermissionResult.allow_all()

        # Employees see only their own
        if self.employee.email == user_context.email:
            return PermissionResult.allow_all()

        return PermissionResult.deny("You can only view your own expenses.")

    def permission_delete(self, user_context: UserContext) -> bool:
        """Only managers and CFO can delete expenses."""
        if user_context.is_superuser:
            return True
        return "manager" in user_context.groups or "cfo" in user_context.groups
