# yourapp/management/commands/cleanup_keycloak_uma.py

from django.core.management.base import BaseCommand
from django.apps import apps

from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from keycloak.exceptions import KeycloakGetError
from keycloak.urls_patterns import (
    URL_ADMIN_CLIENT_AUTHZ_SCOPE_PERMISSION,
)  # has `{scope-id}`:contentReference[oaicite:0]{index=0}


# ─── Monkey‐patch KeycloakAdmin to add a delete for scope‐type permissions ───
def delete_client_authz_scope_permission(self, client_id: str, permission_id: str):
    """
    Delete a scope‐type permission from Keycloak by filling the
    URL_ADMIN_CLIENT_AUTHZ_SCOPE_PERMISSION pattern, which expects
    `{realm-name}`, `{id}` (client UUID) and `{scope-id}`.
    """
    path = URL_ADMIN_CLIENT_AUTHZ_SCOPE_PERMISSION.format(
        **{
            "realm-name": self.connection.realm_name,
            "id": client_id,
            "scope-id": permission_id,
        }
    )
    return self.connection.raw_delete(path)


KeycloakAdmin.delete_client_authz_scope_permission = (
    delete_client_authz_scope_permission
)
# ────────────────────────────────────────────────────────────────────────────────


class Command(BaseCommand):
    help = (
        "Clean up Keycloak UMA resources, client roles and scope-permissions "
        "for each Django model previously registered."
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

        # 2) your client's internal UUID
        client_uuid = "3e5eeafe-a3b3-469e-9db3-54cff7108d70"

        # 3) load existing UMA resource-sets of the django:model type
        existing = kc_uma.resource_set_list()
        django_resources = {
            r["name"]: r for r in existing if r.get("type") == "urn:django:model"
        }

        # 4) define the six scopes
        scopes = ["list", "show", "create", "edit", "delete", "export"]

        # 5) fetch all permissions once and filter for scope-type
        try:
            all_perms = kc_admin.get_client_authz_permissions(client_uuid)
            scope_perms = [p for p in all_perms if p.get("type") == "scope"]
        except KeycloakGetError as e:
            self.stdout.write(self.style.WARNING(f"⚠ Could not list permissions: {e}"))
            scope_perms = []

        # 6) fetch all client-roles once
        try:
            all_roles = kc_admin.get_client_roles(client_uuid)
        except KeycloakGetError as e:
            self.stdout.write(self.style.WARNING(f"⚠ Could not list client roles: {e}"))
            all_roles = []

        # 7) iterate models and delete
        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            resource = django_resources.get(res_name)
            if not resource:
                self.stdout.write(f"⚠ No UMA resource found for {res_name}, skipping.")
                continue

            resource_id = resource.get("_id") or resource.get("id")

            # — delete each scope-permission & its client-role
            for scope in scopes:
                perm_name = f"{res_name}:{scope}"

                # a) delete scope-permission
                perm = next(
                    (p for p in scope_perms if p.get("name") == perm_name), None
                )
                if perm:
                    try:
                        resp = kc_admin.delete_client_authz_scope_permission(
                            client_id=client_uuid, permission_id=perm["id"]
                        )
                        if resp.status_code == 204:
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"🗑 Deleted scope-permission: {perm_name}"
                                )
                            )
                        else:
                            self.stdout.write(
                                self.style.WARNING(
                                    f"❌ Failed deleting permission {perm_name}: HTTP {resp.status_code}"
                                )
                            )
                    except Exception as e:
                        self.stdout.write(
                            self.style.WARNING(
                                f"❌ Error deleting permission {perm_name}: {e}"
                            )
                        )
                else:
                    self.stdout.write(f"⚠ Permission not found: {perm_name}")

                # b) delete client-role of the same name
                role = next((r for r in all_roles if r.get("name") == perm_name), None)
                if role:
                    try:
                        kc_admin.delete_client_role(
                            client_id=client_uuid, role_name=perm_name
                        )
                        self.stdout.write(
                            self.style.SUCCESS(f"🗑 Deleted client-role: {perm_name}")
                        )
                    except KeycloakGetError as e:
                        self.stdout.write(
                            self.style.WARNING(
                                f"❌ Failed deleting role {perm_name}: {e}"
                            )
                        )
                else:
                    self.stdout.write(f"⚠ Role not found: {perm_name}")

            # — finally delete the UMA resource itself
            try:
                kc_uma.resource_set_delete(resource_id)
                self.stdout.write(
                    self.style.SUCCESS(f"🗑 Deleted UMA resource: {res_name}")
                )
            except KeycloakGetError as e:
                self.stdout.write(
                    self.style.WARNING(f"❌ Failed deleting resource {res_name}: {e}")
                )