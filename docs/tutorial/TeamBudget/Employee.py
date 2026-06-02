from django.db import models
from lex.core.models.LexModel import LexModel

from .Team import Team


class Employee(LexModel):
    """A team member linked to a specific team."""

    first_name = models.CharField(max_length=100)
    last_name = models.CharField(max_length=100)
    email = models.EmailField(unique=True)
    team = models.ForeignKey(Team, on_delete=models.CASCADE)
    role = models.CharField(
        max_length=50,
        choices=[
            ("employee", "Employee"),
            ("manager", "Manager"),
            ("cfo", "CFO"),
        ],
        default="employee",
    )

    def __str__(self):
        return f"{self.first_name} {self.last_name}"
