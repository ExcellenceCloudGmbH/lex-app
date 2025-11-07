from django.db.models import TextField


class BokehField(TextField):
    max_length = 1000
    pass