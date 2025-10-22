from django.db.models import TextField


class HTMLField(TextField):
    max_length = 1000
    pass