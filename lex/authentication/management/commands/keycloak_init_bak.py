from django.core.management.base import BaseCommand
import lex.lex_app.settings as settings
from pathlib import Path
from lex.api.views.authentication.KeycloakManager import KeycloakManager


class Command(BaseCommand):
    help = (
        "Register each Django model as a Keycloak UMA resource and "
        "wire up resource-based permissions based on three main policies: admin, standard, and view-only."
    )

    def handle(self, *args, **options):
        # 1) Connect to Keycloak using client credentials

        # payload = KeycloakManager().export_authorization_settings(settings.OIDC_RP_CLIENT_UUID)
        # KeycloakManager().import_authorization_settings(Path('/home/syscall/LUND_IT/.venv/src/lex-app/lex/lex_app/management/commands/perms.json'), settings.OIDC_RP_CLIENT_UUID)
        KeycloakManager().setup_django_model_permissions_scope_based()

        # print(KeycloakManager().export_authorization_settings(settings.OIDC_RP_CLIENT_UUID))
#