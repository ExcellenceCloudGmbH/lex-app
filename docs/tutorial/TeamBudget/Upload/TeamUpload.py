import pandas as pd
from django.db import models
from lex.core.models.CalculationModel import CalculationModel
from lex.audit_logging.handlers.LexLogger import LexLogger

from Input.Team import Team


class TeamUpload(CalculationModel):
    """Upload a CSV file to create Team records."""

    file = models.FileField(
        upload_to="uploads/",
        help_text="CSV with columns: name, budget, manager_email",
    )

    def __str__(self):
        return f"Team Upload — {self.file.name}"

    def calculate(self):
        logger = LexLogger()
        df = pd.read_csv(self.file.path)

        logger.add_heading("Team Upload Results")

        created = 0
        for _, row in df.iterrows():
            team, was_created = Team.objects.update_or_create(
                name=row["name"],
                defaults={
                    "budget": row["budget"],
                    "manager_email": row["manager_email"],
                },
            )
            if was_created:
                created += 1

        logger.add_table(
            headers=["Metric", "Value"],
            rows=[
                ["Rows in CSV", str(len(df))],
                ["Teams created", str(created)],
                ["Teams updated", str(len(df) - created)],
            ],
        )
        logger.log()
