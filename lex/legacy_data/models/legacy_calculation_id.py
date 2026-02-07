from django.db import models
from django_lifecycle import BEFORE_UPDATE, hook


class LegacyCalculationId(models.Model):
    context_id = models.TextField()
    calculation_record = models.TextField()
    calculation_id = models.TextField()

    def __str__(self):
        return self.calculation_id

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
        db_table = 'generic_app_calculationids'
        verbose_name = 'Generic Calculation ID'
        verbose_name_plural = 'Generic Calculation IDs'
