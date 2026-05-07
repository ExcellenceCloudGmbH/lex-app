from django.db import models
from lex.audit_logging.models.AuditLog import AuditLog
from lex.core.models.LexModel import LexModel, PermissionResult

class AuditLogStatus(LexModel):
    # Remove inherited audit fields from LexModel – not needed for status tracking.
    edited_at = None
    created_by = None
    edited_by = None

    audit_log = models.ForeignKey(
        AuditLog,
        related_name='status_records',
        on_delete=models.CASCADE
    )
    status = models.CharField(max_length=20, default='pending')
    error_traceback = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def duration(self):
        """Duration in seconds between created_at and updated_at when status is success or failure."""
        if self.status in ('success', 'failure') and self.created_at and self.updated_at:
            return (self.updated_at - self.created_at).total_seconds()
        return None

    class Meta:
        app_label = 'audit_logging'

    # ------------------------------------------------------------------
    # Permissions: AuditLogStatus is excluded from Keycloak, so we bypass
    # the default scope-based checks and always allow authenticated access.
    # ------------------------------------------------------------------
    def permission_read(self, user_context):
        return PermissionResult.allow_all("AuditLogStatus is Keycloak-excluded")

    def permission_list(self, user_context):
        return True

    def permission_export(self, user_context):
        return PermissionResult.allow_all("AuditLogStatus is Keycloak-excluded")

    def permission_create(self, user_context):
        return False

    def permission_delete(self, user_context):
        return False

    def permission_edit(self, user_context):
        return PermissionResult.deny("AuditLogStatus records are read-only")

    def __str__(self):
        return f"AuditLogStatus({self.audit_log.id}): {self.status}"