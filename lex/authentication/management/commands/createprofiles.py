from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from lex.authentication.models.profile import Profile


class Command(BaseCommand):
    help = "Create a Profile for each User missing one"

    def handle(self, *args, **options):
        created = 0
        for user in User.objects.all():
            profile, was_created = Profile.objects.get_or_create(user=user)
            if was_created:
                created += 1
        total = User.objects.count()
        self.stdout.write(
            self.style.SUCCESS(
                f"Profiles created for {created} users; total users: {total}"
            )
        )
