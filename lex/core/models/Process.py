from django.db.models import Model


class Process(Model):

    class Meta():
        abstract = True
        app_label = 'core'

    def get_structure(self):
        raise NotImplementedError("Subclasses must implement this method")