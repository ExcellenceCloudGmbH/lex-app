import pandas as pd
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.audit_logging.handlers.LexLogger import LexLogger

from Input.Employee import Employee
from Input.Expense import Expense


class ExpenseUpload(CalculationModel):
    """Upload a CSV file to create Expense records."""

    file = models.FileField(
        upload_to="uploads/",
        help_text="CSV with columns: description, amount, category, date, quarter, employee_email",
    )

    def __str__(self):
        return f"Expense Upload — {self.file.name}"

    def calculate(self):
        logger = LexLogger()
        df = pd.read_csv(self.file.path)

        logger.add_heading("Expense Upload Results")

        created = 0
        errors = []
        for _, row in df.iterrows():
            try:
                employee = Employee.objects.get(email=row["employee_email"])
                Expense.objects.create(
                    employee=employee,
                    description=row["description"],
                    amount=row["amount"],
                    category=row["category"],
                    date=row["date"],
                    quarter=row["quarter"],
                )
                created += 1
            except Employee.DoesNotExist:
                errors.append(
                    f"Employee '{row['employee_email']}' not found "
                    f"for expense '{row['description']}'"
                )

        logger.add_table(
            headers=["Metric", "Value"],
            rows=[
                ["Rows in CSV", str(len(df))],
                ["Expenses created", str(created)],
                ["Errors", str(len(errors))],
            ],
        )

        if errors:
            logger.add_heading("Errors", level=2)
            for error in errors:
                logger.add_text(f"⚠️ {error}")

        logger.log()
