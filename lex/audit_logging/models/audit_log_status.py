from django.db import models
from lex.audit_logging.models.audit_log import AuditLog
from lex.core.models.base import LexModel

class AuditLogStatus(LexModel):
    audit_log = models.ForeignKey(
        AuditLog,
        related_name='status_records',
        on_delete=models.CASCADE
    )
    status = models.CharField(max_length=20, default='pending')
    error_traceback = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'audit_logging'

    def __str__(self):
        return f"AuditLogStatus({self.audit_log.id}): {self.status}"