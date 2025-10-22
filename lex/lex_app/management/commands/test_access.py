from django.core.management.base import BaseCommand

from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from keycloak.exceptions import KeycloakGetError


class Command(BaseCommand):
    help = (
        "Register each Django model as a Keycloak UMA resource, "
        "create a client role per resource-scope, "
        "and wire up role-policies & resource-permissions"
    )

    def handle(self, *args, **options):
        # 1) connect via client-credentials
        conn = KeycloakOpenIDConnection(
            server_url="https://exc-testing.com",
            realm_name="lex",
            client_id="LEX_LOCAL_ENV",
            client_secret_key="O1dT6TEXjsQWbRlzVxjwfUnNHPnwDmMF",
            verify=False,
        )
        kc_uma = KeycloakUMA(connection=conn)
        kc_admin = KeycloakAdmin(connection=conn)

        # 2) your client’s internal UUID (hard-coded or lookup)
        client_uuid = "3e5eeafe-a3b3-469e-9db3-54cff7108d70"
        role_name = "test"
        try:
            role = kc_admin.get_client_role(client_uuid, role_name)
            self.stdout.write(f"✔ Client role exists: {role_name}")
        except KeycloakGetError:
            # **correct** call signature: first arg is client_id
            kc_admin.create_client_role(client_uuid, {"name": role_name})
            role = kc_admin.get_client_role(client_uuid, role_name)
            self.stdout.write(self.style.SUCCESS(f"✨ Created client role: {role_name}"))
