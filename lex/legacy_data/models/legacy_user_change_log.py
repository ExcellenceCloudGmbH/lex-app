from django.db import models

class LegacyUserChangeLog(models.Model):
    user_name = models.TextField()
    timestamp = models.DateTimeField()
    message = models.TextField()
    traceback = models.TextField(blank=True, null=True)
    calculationId = models.TextField(db_column='calculationId')
    calculation_record = models.TextField()

    def __str__(self):
        return f"{self.timestamp} - {self.user_name}"

    def can_delete(self, request=None):
        return False

    def can_create(self, request=None):
        return False

    def track(self):
        pass

    def untrack(self):
        pass

    def save(self, *args, **kwargs):
        raise NotImplementedError("This is a legacy archive model. Edits are not allowed.")

    def delete(self, *args, **kwargs):
        raise NotImplementedError("This is a legacy archive model. Deletions are not allowed.")

    class Meta:
        managed = False
        db_table = 'generic_app_userchangelog'
        verbose_name = 'Generic User Change Log'
        verbose_name_plural = 'Generic User Change Logs'
        ordering = ['-timestamp']
