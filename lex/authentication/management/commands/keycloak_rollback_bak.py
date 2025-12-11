from django.core.management.base import BaseCommand
from django.apps import apps
from keycloak import KeycloakOpenIDConnection, KeycloakUMA, KeycloakAdmin
from keycloak.exceptions import KeycloakDeleteError, KeycloakGetError
from lex.lex_app import settings


class Command(BaseCommand):
    help = "Deletes all UMA resources, permissions, and policies created by the setup script."

    def handle(self, *args, **options):
        # 1) Connect to Keycloak
        try:
            self.stdout.write("Connecting to Keycloak...")
            conn = KeycloakOpenIDConnection(
                server_url=settings.KEYCLOAK_URL,
                realm_name=settings.KEYCLOAK_REALM,
                client_id=settings.OIDC_RP_CLIENT_ID,
                client_secret_key=settings.OIDC_RP_CLIENT_SECRET,
                verify=False,
            )
            kc_uma = KeycloakUMA(connection=conn)
            kc_admin = KeycloakAdmin(connection=conn)
            self.stdout.write(self.style.SUCCESS("✔ Connected successfully."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to connect to Keycloak: {e}"))
            return

        # 2) Your client's internal UUID
        client_uuid = settings.OIDC_RP_CLIENT_UUID

        self.stdout.write("Starting rollback process...")

        # --- helpers ---------------------------------------------------------------
        def _authz_base_url(client_id: str) -> str:
            # Prefer SDK helper if present
            client_authz_url = getattr(kc_admin, "client_authz_url", None)
            if callable(client_authz_url):
                return client_authz_url(client_id)
            # Fallback: construct URL
            server = kc_admin.server_url.rstrip("/")
            realm = kc_admin.realm_name
            return f"{server}/admin/realms/{realm}/clients/{client_id}/authz/resource-server"

        def _delete_permission_generic(client_id: str, permission: dict):
            """
            Delete a permission using SDK if available; otherwise raw DELETE to the proper endpoint.
            """
            delete_fn = getattr(kc_admin, "delete_client_authz_permission", None)
            if callable(delete_fn):
                return delete_fn(client_id=client_id, permission_id=permission["id"])

            # Fallback to raw delete by type-specific endpoint
            p_type = (permission.get("type") or "").lower()
            kind = "scope" if p_type == "scope" else "resource"
            url = f"{_authz_base_url(client_id)}/permission/{kind}/{permission['id']}"
            return kc_admin.connection.raw_delete(url)

        # --------------------------------------------------------------------------

        # Preload once for efficient lookups
        try:
            all_permissions = kc_admin.get_client_authz_permissions(
                client_id=client_uuid
            )
            permissions_by_name = {p["name"]: p for p in all_permissions}
        except KeycloakGetError as e:
            self.stderr.write(self.style.ERROR(f"Failed to load permissions: {e}"))
            return

        try:
            all_policies = kc_admin.get_client_authz_policies(client_id=client_uuid)
            policies_by_name = {p["name"]: p for p in all_policies}
        except KeycloakGetError as e:
            self.stderr.write(self.style.ERROR(f"Failed to load policies: {e}"))
            return

        try:
            all_resources = kc_uma.resource_set_list()
            resources_by_name = {r["name"]: r for r in all_resources}
        except KeycloakGetError as e:
            self.stderr.write(self.style.ERROR(f"Failed to load UMA resources: {e}"))
            return

        # Reflects initialization: one permission **per scope** per resource
        scopes = ["list", "show", "create", "edit", "delete", "export"]

        # 3) Iterate over all Django models to delete associated scope-based permissions and resources
        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            self.stdout.write("---")
            self.stdout.write(f"Processing Model: {res_name}")

            # --- Delete the six scope-based permissions created earlier ---
            for scope in scopes:
                perm_name = f"Permission - {res_name} - {scope}"
                permission = permissions_by_name.get(perm_name)
                if not permission:
                    self.stdout.write(
                        f"    - Permission not found, skipping: {perm_name}"
                    )
                    continue
                try:
                    _delete_permission_generic(client_uuid, permission)
                    # Keep local cache consistent
                    permissions_by_name.pop(perm_name, None)
                    self.stdout.write(
                        self.style.SUCCESS(f"    🗑 Deleted permission: {perm_name}")
                    )
                except (KeycloakDeleteError, KeycloakGetError) as e:
                    self.stderr.write(
                        self.style.WARNING(
                            f"    Could not delete permission {perm_name}: {e}"
                        )
                    )
                except Exception as e:
                    self.stderr.write(
                        self.style.WARNING(
                            f"    Unexpected error deleting {perm_name}: {e}"
                        )
                    )

            # --- Delete the UMA resource for the model ---
            resource = resources_by_name.get(res_name)
            if not resource:
                self.stdout.write(f"  - UMA resource not found, skipping: {res_name}")
                continue
            try:
                resource_id = resource["_id"]
                kc_uma.resource_set_delete(resource_id)
                resources_by_name.pop(res_name, None)
                self.stdout.write(
                    self.style.SUCCESS(f"  🗑 Deleted UMA resource: {res_name}")
                )
            except (KeycloakDeleteError, KeycloakGetError) as e:
                self.stderr.write(
                    self.style.WARNING(
                        f"  Could not delete UMA resource {res_name}: {e}"
                    )
                )
            except Exception as e:
                self.stderr.write(
                    self.style.WARNING(
                        f"  Unexpected error deleting UMA resource {res_name}: {e}"
                    )
                )

        # 4) Delete the three core role policies (created as "Policy - <name>")
        self.stdout.write("\n---")
        self.stdout.write("Deleting core policies...")
        for label in ["admin", "standard", "view-only"]:
            policy_name = f"Policy - {label}"
            policy = policies_by_name.get(policy_name)
            if not policy:
                self.stdout.write(f"  - Policy not found, skipping: {policy_name}")
                continue
            try:
                kc_admin.delete_client_authz_policy(
                    client_id=client_uuid, policy_id=policy["id"]
                )
                policies_by_name.pop(policy_name, None)
                self.stdout.write(
                    self.style.SUCCESS(f"  🗑 Deleted policy: {policy_name}")
                )
            except (KeycloakDeleteError, KeycloakGetError) as e:
                self.stderr.write(
                    self.style.WARNING(f"  Could not delete policy {policy_name}: {e}")
                )
            except Exception as e:
                self.stderr.write(
                    self.style.WARNING(
                        f"  Unexpected error deleting policy {policy_name}: {e}"
                    )
                )

        self.stdout.write("\n---")
        self.stdout.write(
            self.style.SUCCESS("Keycloak authorization rollback complete.")
        )