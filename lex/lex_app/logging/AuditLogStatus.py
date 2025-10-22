from django.db import models
from lex.lex_app.logging.AuditLog import AuditLog  # Adjust the import according to your project structure
from lex.lex_app.lex_models.LexModel import LexModel

class AuditLogStatus(LexModel):
    auditlog = models.ForeignKey(
        AuditLog,
        related_name='status_records',
        on_delete=models.CASCADE
    )
    status = models.CharField(max_length=20, default='pending')
    error_traceback = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        app_label = 'lex_app'

    def __str__(self):
        return f"AuditLogStatus({self.auditlog.id}): {self.status}"
