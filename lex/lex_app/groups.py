
from django.contrib.auth.models import Group

ADMIN = 'admin'
STANDARD = 'standard'
VIEW_ONLY = 'view-only'


def create_groups():
    group1, created = Group.objects.get_or_create(name=ADMIN)
    group2, created = Group.objects.get_or_create(name=STANDARD)
    group3, created = Group.objects.get_or_create(name=VIEW_ONLY)
