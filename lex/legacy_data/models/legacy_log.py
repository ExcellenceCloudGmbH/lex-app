from django.db import models


class LegacyLog(models.Model):
    id = models.AutoField(primary_key=True)
    group = models.TextField(blank=True, null=True)
    logfile = models.TextField(blank=True, null=True)
    input_validation = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"LegacyLog({self.id})"

    def can_delete(self, request=None):
        return False

    def can_create(self, request=None):
        return False

    def can_edit(self, request=None):
        return set()

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
        db_table = "generic_app_log"
        verbose_name = "Generic Log"
        verbose_name_plural = "Generic Logs"
