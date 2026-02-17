from django.conf import settings
from django.db import models

class Profile(models.Model):
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="profile",
    )
    uma_permissions = models.JSONField(default=list, blank=True)

    def __str__(self):
        return f"{self.user} Profile"