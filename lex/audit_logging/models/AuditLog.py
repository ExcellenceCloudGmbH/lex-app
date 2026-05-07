from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models
from lex.core.mixins.ModelModificationRestriction import AdminReportsModificationRestriction
from lex.core.models.LexModel import LexModel, PermissionResult

class AuditLog(LexModel):
    ACTION_CHOICES = (
        ('create', 'Create'),
        ('update', 'Update'),
        ('delete', 'Delete'),
    )

    # Remove inherited audit fields from LexModel – AuditLog has its own
    # 'date' and 'author' fields that serve the same purpose.
    created_at = None
    edited_at = None
    created_by = None
    edited_by = None

    modification_restriction = AdminReportsModificationRestriction()
    date = models.DateTimeField(auto_now_add=True)
    author = models.CharField(max_length=255)
    resource = models.CharField(max_length=255)
    action = models.CharField(max_length=10, choices=ACTION_CHOICES)
    payload = models.JSONField(blank=True, null=True)
    calculation_id = models.TextField(default='test_id', null=True, blank=True, verbose_name='Calculation Log')
    calculatable_object = GenericForeignKey("content_type", "object_id")
    content_type = models.ForeignKey(
        ContentType, on_delete=models.CASCADE, null=True, blank=True
    )
    object_id = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        app_label = 'audit_logging'

    # ------------------------------------------------------------------
    # Permissions: AuditLog is excluded from Keycloak, so we bypass
    # the default scope-based checks and always allow authenticated access.
    # ------------------------------------------------------------------
    def permission_read(self, user_context):
        return PermissionResult.allow_all("AuditLog is Keycloak-excluded")

    def permission_list(self, user_context):
        return True

    def permission_export(self, user_context):
        return PermissionResult.allow_all("AuditLog is Keycloak-excluded")

    def permission_create(self, user_context):
        return False  # AuditLogs are system-created only

    def permission_delete(self, user_context):
        return False  # AuditLogs should not be deleted by users

    def permission_edit(self, user_context):
        return PermissionResult.deny("AuditLog records are read-only")

    def __str__(self):
        return f"{self.action} on {self.resource} by {self.author}"

    def to_dict(self):
        """Convert log instance to dictionary with the format expected by the frontend."""
        return {
            "date": self.date.strftime('%Y-%m-%d %H:%M:%S'),
            "author": self.author,
            "resource": self.resource,
            "action": self.action,
            "payload": self.payload or {}
        }