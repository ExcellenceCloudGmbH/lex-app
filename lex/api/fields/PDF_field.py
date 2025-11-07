from django.db.models import FileField


class PDFField(FileField):
    max_length = 300
    pass