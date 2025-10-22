from django.db.models import Model, TextField

from lex.lex_app.rest_api.views.model_entries import One


class CreatedByMixin(Model):
    created_by = TextField(max_length=255, editable=False, default="", null=True, blank=True)

    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        self.created_by = One.user_name
        super().save(*args, **kwargs)

