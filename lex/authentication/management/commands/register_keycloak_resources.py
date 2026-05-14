from django.apps import apps
from django.core.management.base import BaseCommand
from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from lex.lex_app.keycloak_exclusions import is_keycloak_syncable_app


class Command(BaseCommand):
    help = (
        "Register each Django model as a Keycloak UMA resource, "
        "create a client role per resource‐scope, "
        "and wire up role‐policies & scope‐permissions"
    )

    def handle(self, *args, **options):
        # 1) connect via client‐credentials
        conn = KeycloakOpenIDConnection(
            server_url="https://exc-testing.com",
            realm_name="lex",
            client_id="LEX_LOCAL_ENV",
            client_secret_key="O1dT6TEXjsQWbRlzVxjwfUnNHPnwDmMF",
            verify=False,
        )
        kc_uma = KeycloakUMA(connection=conn)
        kc_admin = KeycloakAdmin(connection=conn)

        # 2) your client's internal UUID (hard‐coded or lookup)
        client_uuid = "3e5eeafe-a3b3-469e-9db3-54cff7108d70"

        # 3) load existing UMA resource‐sets
        existing = kc_uma.resource_set_list()
        existing_by_name = {r["name"]: r for r in existing}

        # 4) define the six scopes
        scopes = ["list", "show", "create", "edit", "delete", "export"]

        for app_config in apps.get_app_configs():
            if not is_keycloak_syncable_app(app_config):
                continue
            for model in app_config.get_models():
                if model._meta.abstract or model._meta.proxy:
                    continue
                res_name = f"{model._meta.app_label}.{model.__name__}"

            # — create or skip UMA resource‐set
            if res_name in existing_by_name:
                resource = existing_by_name[res_name]
                resource_id = resource.get("_id") or resource.get("id")
                self.stdout.write(f"✔ UMA resource exists: {res_name}")
            else:
                payload = {
                    "name": res_name,
                    "type": "urn:django:model",
                    "scopes": [{"name": s} for s in scopes],
                    "ownerManagedAccess": False,
                }
                created = kc_uma.resource_set_create(payload)
                resource_id = created.get("_id") or created.get("id")
                self.stdout.write(
                    self.style.SUCCESS(f"✨ Created UMA resource: {res_name}")
                )

            # — for each scope, create a client role + policy + scope‐permission
            for scope in scopes:
                # a) **Client role** name
                role_name = f"{res_name}:{scope}"

                # c) **Scope‐based permission** tying
                #    – this UMA resource,
                #    – this one scope,
                #    – to the policy above.
                perm_name = role_name
                permission_payload = {
                    "name": perm_name,
                    "type": "scope",
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "resources": [resource_id],
                    "scopes": [scope],
                    "policies": [],
                }
                kc_admin.create_client_authz_scope_permission(
                    client_id=client_uuid, payload=permission_payload
                )
                self.stdout.write(
                    self.style.SUCCESS(f"🛡 Created scope permission: {perm_name}")
                )