from django.db import models
from django.contrib.auth.models import User


class Profile(models.Model):
    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    uma_permissions = models.JSONField(default=list, blank=True)

    class Meta:
        app_label = "authentication"

    def __str__(self):
        return f"{self.user.username} Profile"