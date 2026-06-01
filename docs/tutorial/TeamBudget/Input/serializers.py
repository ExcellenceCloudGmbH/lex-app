from rest_framework import serializers
from lex.api.views.model_entries.mixins.PermissionAwareSerializerMixin import add_permission_checks

from Input.Expense import Expense


@add_permission_checks
class ExpenseDefaultSerializer(serializers.ModelSerializer):
    class Meta:
        model = Expense
        fields = "__all__"

    def validate_amount(self, value):
        """Amounts must be positive."""
        if value <= 0:
            raise serializers.ValidationError("Amount must be positive.")
        return value

    def validate(self, attrs):
        """Enforce business rules across fields.

        On partial updates (PATCH) attrs only contains the fields that
        were sent in the request, so we fall back to the existing
        instance for any field the user did not touch.
        """
        amount = attrs.get("amount")
        category = attrs.get("category")

        # Fall back to existing values during partial updates
        if self.instance:
            if amount is None:
                amount = self.instance.amount
            if category is None:
                category = self.instance.category

        if amount and amount > 5000 and category == "meals":
            raise serializers.ValidationError(
                {"amount": "Meal expenses over €5,000 are not allowed."}
            )
        return attrs


Expense.api_serializers = {
    "default": ExpenseDefaultSerializer,
}
