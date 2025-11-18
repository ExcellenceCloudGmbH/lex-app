import json
import logging
import os

from django.apps.registry import apps
from django.conf import settings
from keycloak import (
    KeycloakAdmin,
    KeycloakOpenIDConnection,
    KeycloakUMA,
)
from keycloak.exceptions import KeycloakPostError, KeycloakGetError
import json
import requests
from typing import Union, Dict, Any
from pathlib import Path
import logging

from lex.utilities.decorators.singleton import LexSingleton

# It's good practice to have a dedicated logger
logger = logging.getLogger(__name__)


@LexSingleton
class KeycloakManager:
    """
    A centralized client for managing Keycloak interactions, including both:
    1. Admin operations (managing users, permissions) via the Admin API.
    2. OIDC client operations (token refresh, UMA permissions) for end-users.

    Configuration is pulled from Django's settings.py file.
    Requires `python-keycloak` to be installed.
    """

    def __init__(self):
        """
        Initializes both the Keycloak Admin client and the OpenID Connect client.
        """
        self.realm_name = None
        self.client_uuid = None
        self.oidc = None
        self.admin = None
        self.conn = None
        self.uma = None

        self.initialize()

    def initialize(self):
        self.realm_name = os.getenv("KEYCLOAK_REALM")
        self.client_uuid = os.getenv("OIDC_RP_CLIENT_UUID")

        # Get SSL verification setting from Django settings
        verify_ssl = getattr(settings, "KEYCLOAK_VERIFY_SSL", False)

        try:
            self.conn = KeycloakOpenIDConnection(
                server_url=os.getenv("KEYCLOAK_URL"),
                client_id=os.getenv("OIDC_RP_CLIENT_ID"),
                realm_name=self.realm_name,
                client_secret_key=os.getenv("OIDC_RP_CLIENT_SECRET"),
                verify=verify_ssl,
            )
            self.admin = KeycloakAdmin(connection=self.conn)
            self.uma = KeycloakUMA(connection=self.conn)
            self.oidc = self.conn.keycloak_openid
        except Exception as e:
            logger.error(f"Failed to initialize Keycloak OIDC client: {e}")
            self.oidc = None


    def import_authorization_settings(self, payload: Union[Dict[str, Any], str, Path], client_uuid: str = None) -> bool:
        """
        Import authorization settings into a Keycloak client's authorization server.

        This method uses the POST /admin/realms/{realm}/clients/{client-uuid}/authz/resource-server/import endpoint
        to import authorization configuration (resources, scopes, policies, permissions) from JSON data.

        Args:
            payload (Union[Dict[str, Any], str, Path]): The authorization configuration data.
                Can be:
                - Dict: Python dictionary containing the authorization configuration
                - str: JSON string containing the authorization configuration
                - Path: Path to a JSON file containing the authorization configuration
            client_uuid (str, optional): The client UUID. If not provided, uses self.client_uuid

        Returns:
            bool: True if import was successful, False otherwise

        Raises:
            ValueError: If payload is invalid or client_uuid is not available
            requests.RequestException: If the HTTP request fails
            json.JSONDecodeError: If JSON parsing fails

        Example:
            # Import from dictionary
            config = {
                "policies": [...],
                "resources": [...],
                "scopes": [...],
                "permissions": [...]
            }
            success = keycloak_manager.import_authorization_settings(config)

            # Import from file
            success = keycloak_manager.import_authorization_settings(Path("authz_config.json"))

            # Import from JSON string
            json_str = '{"policies": [], "resources": []}'
            success = keycloak_manager.import_authorization_settings(json_str)
        """
        try:
            # Validate that we have admin client and realm
            if not self.admin:
                logger.error("Keycloak admin client not initialized")
                return False

            if not self.realm_name:
                logger.error("Realm name not configured")
                return False

            # Determine client UUID to use
            target_client_uuid = client_uuid or self.client_uuid
            if not target_client_uuid:
                logger.error("Client UUID not provided and not configured")
                return False

            # Parse the payload into a dictionary
            if isinstance(payload, dict):
                # Already a dictionary
                auth_config = payload
            elif isinstance(payload, (str, Path)):
                if isinstance(payload, Path):
                    # Read from file
                    if not payload.exists():
                        logger.error(f"File not found: {payload}")
                        return False
                    with open(payload, 'r', encoding='utf-8') as f:
                        auth_config = json.load(f)
                else:
                    # Parse JSON string
                    auth_config = json.loads(payload)
            else:
                raise ValueError("Payload must be a dictionary, JSON string, or Path object")

            # Validate that we have a valid configuration structure
            if not isinstance(auth_config, dict):
                raise ValueError("Authorization configuration must be a dictionary")

            # Get the admin access token
            admin_token = self.admin.connection.token['access_token']

            # Prepare the request
            import_url = f"{self.admin.connection.server_url}/admin/realms/{self.realm_name}/clients/{target_client_uuid}/authz/resource-server/import"

            headers = {
                'Authorization': f'Bearer {admin_token}',
                'Content-Type': 'application/json',
                'Accept': 'application/json'
            }

            # Make the import request
            logger.info(f"Importing authorization configuration for client {target_client_uuid}")
            response = requests.post(
                import_url,
                json=auth_config,
                headers=headers,
                # verify=getattr(self.admin.connection, 'verify', True),
                timeout=30
            )

            # Check response status
            if response.status_code == 204:
                logger.info("Authorization configuration imported successfully")
                return True
            else:
                logger.error(
                    f"Failed to import authorization configuration. Status: {response.status_code}, Response: {response.text}")
                return False

        except json.JSONDecodeError as e:
            logger.error(f"Invalid JSON in payload: {e}")
            return False
        except requests.RequestException as e:
            logger.error(f"HTTP request failed: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error during authorization import: {e}")
            return False

    def export_authorization_settings(self, client_uuid: str = None) -> Union[Dict[str, Any], None]:
        """
        Export current authorization settings from a Keycloak client's authorization server.

        This method uses the GET /admin/realms/{realm}/clients/{client-uuid}/authz/resource-server endpoint
        to export the current authorization configuration.

        Args:
            client_uuid (str, optional): The client UUID. If not provided, uses self.client_uuid

        Returns:
            Dict[str, Any]: The authorization configuration if successful, None otherwise

        Raises:
            ValueError: If client_uuid is not available
            requests.RequestException: If the HTTP request fails

        Example:
            # Export current configuration
            config = keycloak_manager.export_authorization_settings()
            if config:
                print(json.dumps(config, indent=2))
        """
        try:
            # Validate that we have admin client and realm
            if not self.admin:
                logger.error("Keycloak admin client not initialized")
                return None

            if not self.realm_name:
                logger.error("Realm name not configured")
                return None

            # Determine client UUID to use
            target_client_uuid = client_uuid or self.client_uuid
            if not target_client_uuid:
                logger.error("Client UUID not provided and not configured")
                return None

            # Get the admin access token
            admin_token = self.admin.connection.token['access_token']

            # Prepare the request
            export_url = f"{self.admin.connection.server_url}/admin/realms/{self.realm_name}/clients/{target_client_uuid}/authz/resource-server/settings"

            headers = {
                'Authorization': f'Bearer {admin_token}',
                'Accept': 'application/json'
            }

            # Make the export request
            logger.info(f"Exporting authorization configuration for client {target_client_uuid}")
            response = requests.get(
                export_url,
                headers=headers,
                # verify=getattr(self.admin.connection, 'verify', True),
                timeout=30
            )

            # Check response status
            if response.status_code == 200:
                logger.info("Authorization configuration exported successfully")
                return response.json()
            else:
                logger.error(
                    f"Failed to export authorization configuration. Status: {response.status_code}, Response: {response.text}")
                return None

        except requests.RequestException as e:
            logger.error(f"HTTP request failed: {e}")
            return None
        except Exception as e:
            logger.error(f"Unexpected error during authorization export: {e}")
            return None

    def setup_django_model_permissions_scope_based(self):
        """
        Initializes Keycloak UMA resources using a **scope-based** permission model.

        Changes from previous version:
        - Creates **one permission per scope per resource** (not one-per-policy).
        - Each permission uses decisionStrategy="AFFIRMATIVE".
        - Policy chains by scope:
            * view-only scopes ("list","read")  -> policies: [admin, standard, view-only]
            * standard-only scopes ("edit","export") -> policies: [admin, standard]
            * admin-only scopes ("create","delete")  -> policies: [admin]
        """
        if not self.admin or not self.uma:
            logger.error("Keycloak clients not initialized. Aborting setup.")
            return

        client_uuid = getattr(settings, "OIDC_RP_CLIENT_UUID", None)
        if not client_uuid:
            logger.error(
                "❌ OIDC_RP_CLIENT_UUID is not configured in settings. Aborting."
            )
            return

        # ---- Helper: create a scope-based permission even if the SDK method doesn't exist
        def _create_scope_permission(client_id: str, payload: dict) -> dict:
            """
            Create a scope-based permission. Uses SDK method if present; otherwise raw POST.
            Returns the created permission JSON.
            """
            create_fn = getattr(
                self.admin, "create_client_authz_scope_based_permission", None
            )
            if callable(create_fn):
                return create_fn(client_id=client_id, payload=payload)

            # Fallback: direct REST call
            import json

            base_url = getattr(self.admin, "client_authz_url", None)
            if callable(base_url):
                url = f"{base_url(client_id)}/permission/scope"
            else:
                server = self.admin.server_url.rstrip("/")
                realm = self.admin.realm_name
                url = f"{server}/admin/realms/{realm}/clients/{client_id}/authz/resource-server/permission/scope"

            resp = self.admin.connection.raw_post(url, data=json.dumps(payload))
            return resp.json() if hasattr(resp, "json") else resp

        # --- 1) Pre-load existing Keycloak configurations
        logger.info("Loading existing Keycloak configurations...")
        try:
            existing_resources = {r["name"]: r for r in self.uma.resource_set_list()}
            existing_roles = {
                r["name"]: r for r in self.admin.get_client_roles(client_id=client_uuid)
            }
            existing_policies = {
                p["name"]: p
                for p in self.admin.get_client_authz_policies(client_id=client_uuid)
            }
            existing_permissions = {
                p["name"]: p
                for p in self.admin.get_client_authz_permissions(client_id=client_uuid)
            }
            logger.info("✔ Configurations loaded.")
        except KeycloakGetError as e:
            logger.error(f"❌ Could not load client configurations: {e.response_body}")
            return

        # --- 2) Ensure core role policies exist
        admin_scopes = ["list", "read", "create", "edit", "delete", "export"]
        standard_scopes = ["list", "read", "edit", "export"]
        view_scopes = ["list", "read"]

        policy_ids = {}
        for role_name in ["admin", "standard", "view-only"]:
            role_id = existing_roles.get(role_name, {}).get("id")
            if not role_id:
                logger.warning(
                    f"  - Role '{role_name}' not found. Please ensure it exists."
                )
                continue

            full_policy_name = f"Policy - {role_name}"
            policy = existing_policies.get(full_policy_name)
            if policy:
                policy_ids[role_name] = policy["id"]
                logger.info(f"  ✔ Policy exists: {full_policy_name}")
            else:
                try:
                    roles_config = [{"id": role_id, "required": True}]
                    policy_payload = {
                        "name": full_policy_name,
                        "type": "role",
                        "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS",
                        "roles": roles_config,
                    }
                    created_policy = self.admin.create_client_authz_role_based_policy(
                        client_id=client_uuid, payload=policy_payload
                    )
                    policy_ids[role_name] = created_policy["id"]
                    existing_policies[full_policy_name] = created_policy
                    logger.info(f"  ✨ Created role policy: {full_policy_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create policy {full_policy_name}: {e}")

        def _policies_for_scope(scope: str):
            """Return the list of policy IDs required for a given scope (AFFIRMATIVE)."""
            chain_names = (
                ["admin", "standard", "view-only"]
                if scope in view_scopes
                else ["admin", "standard"]
                if scope in standard_scopes
                else ["admin"]  # create/delete fall here
            )
            missing = [n for n in chain_names if n not in policy_ids]
            if missing:
                logger.warning(
                    f"    - Skipping scope '{scope}': missing policies {missing}"
                )
                return None
            return [policy_ids[n] for n in chain_names]

        # --- 3) For each model: ensure UMA resource & one scope-permission per scope
        all_scopes = admin_scopes[:]  # full set used for UMA resource definition

        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            logger.info(f"\n--- Processing Model: {res_name} ---")

            # a) Ensure UMA resource with all scopes
            resource = existing_resources.get(res_name)
            if resource:
                resource_id = resource.get("_id")
                logger.info(f"  ✔ UMA resource exists: {res_name}")
            else:
                try:
                    payload = {
                        "name": res_name,
                        "scopes": [{"name": s} for s in all_scopes],
                    }
                    created = self.uma.resource_set_create(payload)
                    resource_id = created.get("_id")
                    existing_resources[res_name] = created
                    logger.info(f"  ✨ Created UMA resource: {res_name}")
                except Exception as e:
                    logger.error(f"  ❌ Failed to create resource {res_name}: {e}")
                    continue

            if not resource_id:
                logger.error(
                    f"  ❌ Could not get resource ID for {res_name}. Skipping permissions."
                )
                continue

            # b) Create or update **one permission per scope**
            for scope in all_scopes:
                chain_ids = _policies_for_scope(scope)
                if not chain_ids:
                    continue

                perm_name = f"Permission - {res_name} - {scope}"
                desired_payload = {
                    "name": perm_name,
                    "type": "scope",
                    "logic": "POSITIVE",
                    "decisionStrategy": "AFFIRMATIVE",  # << as requested
                    "resources": [resource_id],  # constrain to this resource
                    "policies": chain_ids,  # policy chain per scope
                    "scopes": [scope],  # single scope per permission
                }

                existing = existing_permissions.get(perm_name)
                if existing:
                    try:
                        # Normalize and check whether update is needed
                        current_scopes = existing.get("scopes") or []
                        current_scope_names = [
                            s.get("name", s) if isinstance(s, dict) else s
                            for s in current_scopes
                        ]
                        current_policies = existing.get("policies") or []
                        current_policy_ids = [
                            p.get("id", p) if isinstance(p, dict) else p
                            for p in current_policies
                        ]
                        needs_update = (
                            set(current_scope_names) != {scope}
                            or set(current_policy_ids) != set(chain_ids)
                            or existing.get("decisionStrategy") != "AFFIRMATIVE"
                            or set(existing.get("resources") or []) != {resource_id}
                        )
                        if needs_update:
                            payload = dict(existing)
                            payload.update(desired_payload)
                            self.admin.update_client_authz_permission(
                                client_id=client_uuid,
                                permission_id=existing["id"],
                                payload=payload,
                            )
                            logger.info(f"    🔄 Updated permission: {perm_name}")
                        else:
                            logger.info(f"    ✔ Permission up-to-date: {perm_name}")
                    except Exception as e:
                        logger.error(
                            f"    ❌ Failed to update existing permission {perm_name}: {e}"
                        )
                    continue

                # Create new permission
                try:
                    created_perm = _create_scope_permission(
                        client_uuid, desired_payload
                    )
                    existing_permissions[perm_name] = created_perm
                    logger.info(
                        f"    🛡️  Created scope permission '{perm_name}' "
                        f"(policies: {', '.join(['admin', 'standard', 'view-only'] if scope in view_scopes else ['admin', 'standard'] if scope in standard_scopes else ['admin'])})"
                    )
                except KeycloakPostError as e:
                    logger.error(
                        f"    ❌ Failed to create scope permission {perm_name}: {e.response_body}"
                    )
                except Exception as e:
                    logger.error(
                        f"    ❌ An unexpected error occurred for permission {perm_name}: {e}"
                    )

        logger.info("\n✅ Keycloak scope-based setup complete.")

    def get_uma_permissions(self, access_token: str, permissions: list = None):
        """
        Fetches UMA (User-Managed Access) permissions for a given access token.
        This encapsulates the logic from your `helpers.py`.

        Args:
            access_token (str): The user's current access token.

        Returns:
            A dict of UMA permissions or None if an error occurs.
        """
        if not self.oidc:
            logger.error("OIDC client not initialized. Cannot fetch UMA permissions.")
            return None

        try:
            return self.oidc.uma_permissions(
                token=access_token, permissions=permissions
            )
        except Exception as e:
            logger.error(f"Failed to fetch UMA permissions: {e}")
            return None

    def refresh_user_token(self, refresh_token: str):
        """
        Refreshes a user's access token using their refresh token.
        This encapsulates the logic from your `RefreshTokenSessionMiddleware`.

        Args:
            refresh_token (str): The user's refresh token.

        Returns:
            A dict with new tokens ('access_token', 'refresh_token', etc.) or None.
        """
        if not self.oidc:
            logger.error("OIDC client not initialized. Cannot refresh token.")
            if not self.retry():
                return None

        try:
            return self.oidc.refresh_token(refresh_token)
        except KeycloakPostError as e:
            logger.warning(
                f"Failed to refresh token: {e.response_code} - {e.response_body}"
            )
            return None

    def get_user_permissions(self, access_token: str, model_or_instance):
        """
        Gets the allowed actions for a user on a specific Django model or model instance.

        Args:
            access_token (str): The user's access token.
            model_or_instance: A Django model class or an instance of a model.

        Returns:
            A set of allowed scopes (e.g., {'view', 'edit'}).
        """
        if not self.oidc:
            logger.error("OIDC client not initialized.")
            if not self.retry():
                return set()

        try:
            uma_permissions = self.oidc.uma_permissions(token=access_token)

            # Determine the resource name
            if hasattr(model_or_instance, "_meta"):  # It's an instance or a model class
                app_label = model_or_instance._meta.app_label
                model_name = model_or_instance._meta.model_name
                resource_name = f"{app_label}.{model_name}"
            else:
                return set()

            allowed_scopes = set()
            for perm in uma_permissions:
                if perm.get("rsname") == resource_name:
                    # Check for record-specific permissions if an instance is provided
                    if hasattr(model_or_instance, "pk") and model_or_instance.pk:
                        if perm.get("resource_set_id") == str(model_or_instance.pk):
                            allowed_scopes.update(perm.get("scopes", []))
                    else:  # General model permissions
                        allowed_scopes.update(perm.get("scopes", []))

            return allowed_scopes

        except Exception as e:
            logger.error(f"Failed to get UMA permissions: {e}")
            return set()

    def setup_django_model_permissions(self):
        """
        Initializes Keycloak UMA resources and permissions for all Django models.
        This is a refactoring of your keycloak_init_bak.py script.
        """
        if not self.admin:
            logger.error("Admin client not initialized.")
            if not self.retry():
                return

        # 3) Pre-load existing Keycloak authorization configurations
        logger.info("Loading existing Keycloak configurations...")
        client_uuid = settings.OIDC_RP_CLIENT_UUID or ""
        try:
            existing_resources = {r["name"]: r for r in self.uma.resource_set_list()}
            existing_roles = {
                r["name"]: r for r in self.admin.get_client_roles(client_id=client_uuid)
            }
            existing_policies = {
                p["name"]: p
                for p in self.admin.get_client_authz_policies(client_id=client_uuid)
            }
            existing_permissions = {
                p["name"]: p
                for p in self.admin.get_client_authz_permissions(client_id=client_uuid)
            }
            logger.info("✔ Configurations loaded.")
        except KeycloakGetError as e:
            logger.error(
                f"\n❌ Could not load client configurations. "
                f"Please check if client UUID '{client_uuid}' is correct and has Authorization enabled."
            )
            logger.error(f"   Keycloak error: {e}")
            return

        # 4) Define the three policies and which scopes they grant
        policy_definitions = {
            "admin": ["list", "read", "create", "edit", "delete", "export"],
            "standard": ["list", "read", "create", "edit", "export"],
            "view-only": ["list", "read"],
        }
        policy_ids = {}

        logger.info("---")
        logger.info("Setting up core policies: admin, standard, view-only...")

        for policy_name in policy_definitions.keys():
            role_id = None
            policy_id = None

            # a) Create or get client role for the policy
            try:
                if policy_name in existing_roles:
                    role_id = existing_roles[policy_name]["id"]
                    logger.info(f"  ✔ Client role exists: {policy_name}")
                else:
                    # Try to get existing role first
                    try:
                        role = self.admin.get_client_role(
                            client_id=client_uuid, role_name=policy_name
                        )
                        role_id = role["id"]
                        existing_roles[policy_name] = role
                        logger.info(f"  ✔ Client role found: {policy_name}")
                    except:
                        # Role doesn't exist, create it
                        role_payload = {
                            "name": policy_name,
                            "description": f"Role for {policy_name} policy",
                            "clientRole": True,
                            "composite": False,
                        }
                        self.admin.create_client_role(
                            client_role_id=client_uuid,
                            payload=role_payload,
                            skip_exists=True,
                        )
                        role = self.admin.get_client_role(
                            client_id=client_uuid, role_name=policy_name
                        )
                        role_id = role["id"]
                        existing_roles[policy_name] = role
                        logger.info(f"  ✨ Created client role: {policy_name}")

            except Exception as e:
                logger.error(f"❌ Failed to create/get role {policy_name}: {e}")
                continue

            # b) Create or get role-based policy
            # b) Create or get role-based policy
            try:
                full_policy_name = f"Policy - {policy_name}"

                if full_policy_name in existing_policies:
                    policy_id = existing_policies[full_policy_name]["id"]
                    logger.info(f"  ✔ Role policy exists: {full_policy_name}")
                else:
                    # Alternative approach: Try using the generic policy creation method
                    try:
                        # Method 1: Use the specific role-based policy creation
                        roles_config = [{"id": role_id, "required": True}]
                        policy_payload = {
                            "name": full_policy_name,
                            "type": "role",
                            "logic": "POSITIVE",
                            "decisionStrategy": "UNANIMOUS",
                            "config": {
                                "roles": json.dumps(roles_config, separators=(",", ":"))
                            },
                        }

                        created_policy = (
                            self.admin.create_client_authz_role_based_policy(
                                client_id=client_uuid, payload=policy_payload
                            )
                        )

                    except Exception as role_policy_error:
                        # Method 2: Fallback to generic policy creation
                        logger.info(
                            f"    ⚠ Role-based policy creation failed, trying generic method..."
                        )

                        policy_payload = {
                            "name": full_policy_name,
                            "type": "role",
                            "logic": "POSITIVE",
                            "decisionStrategy": "UNANIMOUS",
                            "config": {
                                "roles": f'[{{"id":"{role_id}","required":true}}]'  # String format instead of JSON
                            },
                        }

                        # Debug output
                        logger.info(
                            f"    🔍 Trying with config: {policy_payload['config']}"
                        )

                        created_policy = self.admin.create_client_authz_policy(
                            client_id=client_uuid, payload=policy_payload
                        )

                    # Handle different response formats
                    if isinstance(created_policy, dict) and "id" in created_policy:
                        policy_id = created_policy["id"]
                    else:
                        # If response doesn't contain ID, refresh policies and find it
                        logger.info(
                            f"    🔄 Refreshing policies to find created policy..."
                        )
                        updated_policies = self.admin.get_client_authz_policies(
                            client_id=client_uuid
                        )
                        for policy in updated_policies:
                            if policy["name"] == full_policy_name:
                                policy_id = policy["id"]
                                break

                    if policy_id:
                        existing_policies[full_policy_name] = {
                            "id": policy_id,
                            "name": full_policy_name,
                        }
                        logger.info(f"  ✨ Created role policy: {full_policy_name}")
                    else:
                        raise Exception("Policy created but ID not found")

            except Exception as e:
                logger.error(f"❌ Failed to create policy {full_policy_name}: {e}")
                # Add more detailed error information
                if hasattr(e, "response") and hasattr(e.response, "text"):
                    logger.error(f"    Response: {e.response.text}")
                continue
            # Only add to policy_ids if both role and policy were created successfully
            if role_id and policy_id:
                policy_ids[policy_name] = policy_id
                logger.info(
                    f"  ✅ Policy setup complete for: {policy_name} (ID: {policy_id})"
                )
            else:
                logger.error(f"❌ Failed to setup policy: {policy_name}")

        # Debug: Print policy_ids to verify they were created
        logger.info(f"\n🔍 Policy IDs created: {policy_ids}")

        if not policy_ids:
            logger.error(
                "\n❌ No policies were created successfully. Cannot proceed with permissions."
            )
            return

        # 5) Iterate over all Django models to create resources and permissions
        scopes = ["list", "read", "create", "edit", "delete", "export"]

        for model in apps.get_models():
            res_name = f"{model._meta.app_label}.{model.__name__}"
            logger.info("---")
            logger.info(f"Processing Model: {res_name}")

            # --- Create or fetch UMA resource-set for the model ---
            if res_name in existing_resources:
                resource_id = existing_resources[res_name].get(
                    "_id"
                ) or existing_resources[res_name].get("id")
                logger.info(f"  ✔ UMA resource exists: {res_name}")
            else:
                payload = {
                    "name": res_name,
                    "displayName": res_name,
                    "type": "django_model",
                    "scopes": [{"name": s} for s in scopes],
                    "ownerManagedAccess": False,
                }
                try:
                    created = self.uma.resource_set_create(payload)
                    resource_id = created.get("_id") or created.get("id")
                    existing_resources[res_name] = created
                    logger.info(f"  ✨ Created UMA resource: {res_name}")
                except Exception as e:
                    logger.error(f"Failed to create resource {res_name}: {e}")
                    continue

            # --- Create one resource-based permission per policy (not per scope) ---
            for policy_name, scopes_for_policy in policy_definitions.items():
                # Skip if this policy wasn't created successfully
                if policy_name not in policy_ids:
                    logger.info(f"    ⏭ Skipping policy {policy_name} - not available")
                    continue

                perm_name = f"Permission - {res_name} - {policy_name}"

                if perm_name in existing_permissions:
                    logger.info(f"    ✔ Resource permission exists: {perm_name}")
                    continue

                # Get the policy ID for this policy
                policy_id = policy_ids[policy_name]

                # Create resource-based permission that grants the scopes defined for this policy
                permission_payload = {
                    "name": perm_name,
                    "type": "resource",  # Changed from "scope" to "resource"
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "resources": [resource_id],
                    "scopes": scopes_for_policy,  # All scopes this policy grants for this resource
                    "policies": [policy_id],  # Link to the specific policy
                }

                try:
                    self.admin.create_client_authz_resource_based_permission(
                        client_id=client_uuid, payload=permission_payload
                    )
                    existing_permissions[perm_name] = {"name": perm_name}
                    logger.info(f"    🛡 Created resource permission: {perm_name}")
                    logger.info(
                        f"        └── Grants scopes: {', '.join(scopes_for_policy)}"
                    )

                except KeycloakPostError as e:
                    logger.error(
                        f"    ❌ Failed to create permission {perm_name}: {e.response.text if hasattr(e, 'response') else e}"
                    )
                except Exception as e:
                    logger.error(f"    ❌ Failed to create permission {perm_name}: {e}")

        logger.info("\n---")
        logger.info("Keycloak authorization setup complete.")

        # Print summary
        logger.info("\n📊 Summary:")
        total_models = len([m for m in apps.get_models()])
        total_policies = len(policy_definitions)
        max_permissions = total_models * total_policies
        logger.info(f"  • Models processed: {total_models}")
        logger.info(f"  • Policies created: {total_policies}")
        logger.info(f"  • Max permissions possible: {max_permissions}")
        logger.info("\n🔐 Permission Structure:")
        for policy_name, scopes in policy_definitions.items():
            logger.info(f"  • {policy_name}: {', '.join(scopes)}")

    def get_authz_permissions(self):
        permissions = self.admin.get_client_authz_permissions(
            client_id=self.client_uuid
        )
        return permissions

    def get_permissions(
        self, access_token: str, resource_name: str, resource_id: str = None
    ):
        """
        Gets the allowed actions for a user on a specific resource.

        Args:
            access_token (str): The user's access token.
            resource_name (str): The name of the resource (e.g., 'lex_app.MyModel').
            resource_id (str, optional): The specific ID of the record. Defaults to None.

        Returns:
            A set of allowed scopes (e.g., {'edit', 'view'}).
        """
        if not self.oidc:
            logger.error("OIDC client not initialized.")
            if self.retry():
                return set()
        try:
            uma_permissions = self.uma.resource_set_list()
            allowed_scopes = set()
            for perm in uma_permissions:
                if perm.get("rsname") == resource_name:
                    if resource_id and perm.get("resource_set_id") == resource_id:
                        allowed_scopes.update(perm.get("scopes", []))
                    elif not resource_id:
                        allowed_scopes.update(perm.get("scopes", []))
            return allowed_scopes
        except Exception as e:
            logger.error(f"Failed to get UMA permissions: {e}")
            return set()

    def teardown_django_model_permissions(self):
        pass