"""``lex create_api_key`` – issue a scoped API key that acts as a user.

    lex create_api_key --user quantium-sync --name "Quantium sync" \\
        --scopes read,list --prefix api/myproject/

The key is printed once; only its hash is stored.
"""
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from lex.authentication.models import LexAPIKey


class Command(BaseCommand):
    help = "Create a scoped API key that authenticates as an existing user."

    def add_arguments(self, parser):
        parser.add_argument("--user", required=True, help="Username the key acts as.")
        parser.add_argument("--name", default=None, help="Label for the key (default: the username).")
        parser.add_argument(
            "--scopes", default="", help="Comma-separated scopes, e.g. read,list,edit (default: none)."
        )
        parser.add_argument(
            "--prefix", action="append", default=[],
            help="Path prefix the key may use, e.g. api/myproject/ (repeatable; default: anywhere).",
        )
        parser.add_argument("--expires-in-days", type=int, default=None, help="Expire the key after N days.")

    def handle(self, *args, **options):
        user = get_user_model()._default_manager.filter(username=options["user"]).first()
        if user is None:
            raise CommandError(f"No user named {options['user']!r}.")
        scopes = [scope.strip() for scope in options["scopes"].split(",") if scope.strip()]
        expiry = None
        if options["expires_in_days"]:
            expiry = timezone.now() + timedelta(days=options["expires_in_days"])
        _, raw_key = LexAPIKey.objects.create_key(
            name=(options["name"] or user.get_username())[:50],
            user=user,
            scopes=scopes,
            allowed_path_prefixes=options["prefix"],
            expiry_date=expiry,
        )
        self.stdout.write(raw_key)
        self.stderr.write("Store this key now: it is shown only once.")
