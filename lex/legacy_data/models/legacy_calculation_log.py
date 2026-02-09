from django.db import models


class LegacyCalculationLog(models.Model):
    timestamp = models.DateTimeField()
    trigger_name = models.TextField(blank=True, null=True)
    message_type = models.TextField()
    calculationId = models.TextField(db_column='calculationId')
    message = models.TextField()
    method = models.TextField()
    is_notification = models.BooleanField()
    calculation_record = models.TextField()

    def __str__(self):
        return f"{self.timestamp} - {self.message_type}"

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
        db_table = 'generic_app_calculationlog'
        verbose_name = 'Generic Calculation Log'
        verbose_name_plural = 'Generic Calculation Logs'
        ordering = ['-timestamp']
