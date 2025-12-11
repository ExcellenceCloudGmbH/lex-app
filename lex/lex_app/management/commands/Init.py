# core/management/commands/Init.py
import json
import time
import uuid
import logging
import traceback
from copy import deepcopy
from pathlib import Path

import requests
from django.core.management import call_command
from django.core.management.base import BaseCommand
from django.apps import apps
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ProjectState
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.operations.models import CreateModel, DeleteModel, RenameModel
from typing import Dict, List, Tuple, Set, Any

from core.management.commands.bootstrap_callback_server import start_callback_server

logger = logging.getLogger(__name__)
# core/management/commands/Init.py

import os
import uuid
import webbrowser
from urllib.parse import urlencode

from django.conf import settings
import os
import time
import uuid
import json
import socket
import asyncio
import threading
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from django.conf import settings
import os


KEYCLOAK_ENV_VARS = [
    "KEYCLOAK_URL",
    "KEYCLOAK_REALM",
    "KEYCLOAK_REALM_NAME",
    "OIDC_RP_CLIENT_ID",
    "OIDC_RP_CLIENT_SECRET",
    "OIDC_RP_CLIENT_UUID",
]

STATE_FILE = Path(
    getattr(settings, "KEYCLOAK_STATE_FILE", Path(os.getcwd() or settings.BASE_DIR) / ".keycloak_state.json")
)
ENV_FILE = Path(os.getcwd() or settings.BASE_DIR) / ".env"


def get_missing_keycloak_env() -> list[str]:
    missing = []
    for key in KEYCLOAK_ENV_VARS:
        if key in ("KEYCLOAK_REALM", "KEYCLOAK_REALM_NAME"):
            if not (
                os.getenv("KEYCLOAK_REALM")
                or os.getenv("KEYCLOAK_REALM_NAME")
                or getattr(settings, "KEYCLOAK_REALM_NAME", None)
            ):
                missing.append("KEYCLOAK_REALM/KEYCLOAK_REALM_NAME")
            continue
        if not (os.getenv(key) or getattr(settings, key, None)):
            missing.append(key)
    return missing


def build_instance_controller_url(state: str, callback_url: str) -> str:
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("INSTANCE_CONTROLLER_BASE_URL is not configured")
    params = {
        "state": state,
        "callback": callback_url,
        "flow": "keycloak-client-bootstrap",
        "project": getattr(settings, "LEX_PROJECT_SLUG", "lex-app"),
    }
    return f"{base}/lex/keycloak-bootstrap?{urlencode(params)}"


def _load_state_map() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8") or "{}")
    except Exception:
        return {}


def get_state(state: str) -> str | None:
    data = _load_state_map()
    return data.get(state)


def wait_for_keycloak_setup(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> bool:
    start = time.time()
    while True:
        if not get_missing_keycloak_env():
            print("Keycloak credentials detected; continuing Init.")
            return True

        current = get_state(state)
        if current == "done":
            print("Keycloak setup completed; continuing Init.")
            return True
        if current == "cancelled":
            print("Keycloak setup cancelled; stopping Init.")
            return False

        if time.time() - start > timeout_seconds:
            print(f"Timed out waiting for Keycloak setup after {timeout_seconds} seconds.")
            return False

        time.sleep(poll_interval)

def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def start_local_server_if_needed(host: str = "127.0.0.1", port: int = 9002) -> None:
    if _port_in_use(host, port):
        return

    def run_uvicorn():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        uvicorn.run(
            "lex_app.asgi:application",
            host=host,
            port=port,
            loop="asyncio",
            reload=False,
            log_level="info",
        )

    t = threading.Thread(target=run_uvicorn, daemon=True)
    t.start()



def _save_state_map(data: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(STATE_FILE)


def set_state(state: str, value: str) -> None:
    data = _load_state_map()
    data[state] = value
    _save_state_map(data)




def poll_bootstrap_status(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> dict | None:
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    if not base:
        raise RuntimeError("INSTANCE_CONTROLLER_BASE_URL not configured")

    url = f"{base}/api/lex/bootstrap_status/{state}"
    start = time.time()

    while True:
        if time.time() - start > timeout_seconds:
            print(f"Timed out waiting for Keycloak setup after {timeout_seconds} seconds.")
            return None

        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"Error polling bootstrap status: {e}")
            time.sleep(poll_interval)
            continue

        status_val = data.get("status")
        if status_val == "done":
            payload = data.get("payload")
            if payload:
                print("Keycloak credentials retrieved from instance-controller.")
                return payload
            else:
                print("Status is done but payload is missing; retrying...")
                time.sleep(poll_interval)
                continue
        elif status_val == "cancelled":
            print("Keycloak setup cancelled.")
            return None

        # still pending, keep polling
        time.sleep(poll_interval)


def update_env_file(values: dict) -> None:
    env_file = Path(settings.BASE_DIR) / ".env"
    text = ""
    if env_file.exists():
        text = env_file.read_text(encoding="utf-8")

    lines = text.splitlines()
    kv = {}
    non_kv_lines = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            non_kv_lines.append(line)
            continue
        k, v = stripped.split("=", 1)
        kv[k] = v

    for k, v in values.items():
        kv[k] = v

    out_lines = []
    out_lines.extend(non_kv_lines)
    for k, v in kv.items():
        out_lines.append(f"{k}={v}")

    tmp = env_file.with_suffix(".env.tmp")
    tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
    tmp.replace(env_file)

def initiate_device_flow():
    state = str(uuid.uuid4())
    # Your equivalent of /oauth/device/authorize
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    resp = requests.post(
        f"{base}/api/lex/bootstrap/initiate",
        json={"state": state},
    )
    resp.raise_for_status()
    data = resp.json()
    # returns: {"state": "...", "verification_uri": "https://.../lex/keycloak-bootstrap?state=..."}
    return data["state"], data["verification_uri"]

def poll_for_credentials(state, interval=5, timeout=900):
    start = time.time()
    while True:
        if time.time() - start > timeout:
            return None  # timeout

        base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
        resp = requests.get(
            f"{base}/api/lex/bootstrap_status/{state}"
        )
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "done":
            return data["payload"]  # dict with 5 env vars
        if data.get("status") == "cancelled":
            return None

        time.sleep(interval)

def fetch_bootstrap_state(state: str) -> dict | None:
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")

    url = f"{base}/api/lex/bootstrap_status/{state}"
    resp = requests.get(url, timeout=5)
    resp.raise_for_status()
    data = resp.json()
    if data.get("status") == "done" and data.get("payload"):
        return data["payload"]
    if data.get("status") == "cancelled":
        return {"cancelled": True}
    return None

def wait_for_keycloak_setup_v2(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> dict | None:
    start = time.time()
    while True:
        # if env already set, short-circuit
        if not get_missing_keycloak_env():
            return {
                "KEYCLOAK_URL": os.getenv("KEYCLOAK_URL", ""),
                "KEYCLOAK_REALM": os.getenv("KEYCLOAK_REALM", ""),
                "OIDC_RP_CLIENT_ID": os.getenv("OIDC_RP_CLIENT_ID", ""),
                "OIDC_RP_CLIENT_SECRET": os.getenv("OIDC_RP_CLIENT_SECRET", ""),
                "OIDC_RP_CLIENT_UUID": os.getenv("OIDC_RP_CLIENT_UUID", ""),
            }

        state_data = fetch_bootstrap_state(state)
        if state_data:
            if state_data.get("cancelled"):
                return None
            return state_data  # dict with 5 keys

        if time.time() - start > timeout_seconds:
            return None

        time.sleep(poll_interval)

class KeycloakSyncManager:
    """Handles the sync between Django models and Keycloak resources/permissions"""

    def __init__(self):
        from lex.api.views.authentication.KeycloakManager import KeycloakManager
        # Parse .env file and update os.environ
        from dotenv import dotenv_values
        env_config = dotenv_values('.env')
        for key, value in env_config.items():
            os.environ[key] = value
        self.kc_manager = KeycloakManager()
        self.default_scopes = ['list', 'read', 'create', 'edit', 'delete', 'export']
        self.exported_configs = None

    def get_all_django_models(self) -> Set[str]:
        """Get all Django model names in the format 'app_label.ModelName'"""
        all_models = set()

        for app_config in apps.get_app_configs():
            app_label = app_config.label

            # Skip Django's built-in apps that we don't want to sync
            skip_apps = {
                'admin', 'auth', 'contenttypes', 'sessions', 'messages',
                'staticfiles', 'migrations', 'django_extensions'
            }

            if app_label in skip_apps:
                logger.debug(f"Skipping built-in app: {app_label}")
                continue

            try:
                for model in app_config.get_models():
                    # Skip abstract models
                    if model._meta.abstract:
                        logger.debug(f"Skipping abstract model: {app_label}.{model.__name__}")
                        continue

                    # Skip proxy models (they don't need separate permissions)
                    if model._meta.proxy:
                        logger.debug(f"Skipping proxy model: {app_label}.{model.__name__}")
                        continue

                    model_name = f"{app_label}.{model.__name__}"
                    all_models.add(model_name)
                    logger.debug(f"Found model: {model_name}")

            except Exception as e:
                logger.warning(f"Error getting models for app {app_label}: {e}")

        logger.info(f"Found {len(all_models)} Django models total")
        return all_models


    def export_configs(self):
        if self.exported_configs:
            return self.exported_configs
        self.exported_configs = self.kc_manager.export_authorization_settings()
        return self.exported_configs

    def get_existing_keycloak_resources(self, auth_config: Dict) -> Set[str]:
        """Get all resource names that exist in Keycloak config"""
        existing_resources = set()

        for resource in auth_config.get('resources', []):
            resource_name = resource.get('name')
            if resource_name:
                existing_resources.add(resource_name)

        logger.info(f"Found {len(existing_resources)} existing Keycloak resources")
        return existing_resources

    def find_missing_models(self, all_django_models: Set[str], existing_keycloak_resources: Set[str],
                            to_delete_set: Set[str]) -> Set[str]:
        """Find Django models that are missing from Keycloak and should be added"""
        # Models that exist in Django but not in Keycloak (excluding ones we're about to delete)
        missing_models = all_django_models - existing_keycloak_resources - to_delete_set

        if missing_models:
            logger.info(f"Found {len(missing_models)} models missing from Keycloak:")
            for model_name in sorted(missing_models):
                logger.info(f"  Missing: {model_name}")
        else:
            logger.info("No missing models found - all Django models are synced with Keycloak")

        return missing_models
    def find_permissions_for_resource_name(self, resource_name: str, auth_config: Dict) -> List[Dict[str, Any]]:
        """Find all permissions that reference a specific resource name in the policies array"""
        resource_permissions = []

        for policy in auth_config.get('policies', []):
            if policy.get('type') == 'scope':  # This is actually a permission
                config = policy.get('config', {})
                resources_str = config.get('resources', '[]')
                try:
                    resources = json.loads(resources_str) if isinstance(resources_str, str) else resources_str
                    if resource_name in resources:
                        resource_permissions.append(policy)
                except:
                    pass

        return resource_permissions

    def delete_resources_individual(self, to_delete_set: Set[str], all_resources: List[Dict]) -> bool:
        """Delete resources using individual API calls - reuse existing resource data"""
        try:
            deletion_success = 0

            logger.info(f"Using existing resource data with {len(all_resources)} resources")

            # Create a mapping using the resource data we already have (NO additional API calls!)
            resource_name_to_id = {}

            for resource in all_resources:
                resource_name = resource.get('name')
                if resource_name in to_delete_set:
                    # Get the _id field from the existing resource data
                    resource_id = resource.get('_id')
                    if resource_id:
                        resource_name_to_id[resource_name] = resource_id
                        logger.info(f"Found resource to delete: {resource_name} -> {resource_id}")

            logger.info(f"Found {len(resource_name_to_id)} resources to delete")

            # Delete each resource found
            for resource_name in to_delete_set:
                if resource_name not in resource_name_to_id:
                    logger.warning(f"Resource {resource_name} not found, may already be deleted")
                    deletion_success += 1
                    continue

                resource_id = resource_name_to_id[resource_name]

                try:
                    # Get permissions for this resource and delete them first
                    permissions = self.kc_manager.admin.get_client_authz_permissions(
                        client_id=self.kc_manager.client_uuid
                    )

                    for permission in permissions:
                        perm_resources = permission.get('resources', [])
                        if resource_id in perm_resources:
                            perm_id = permission.get('id')
                            perm_name = permission.get('name')

                            if perm_id:
                                try:
                                    self.kc_manager.admin.delete_client_authz_permission(
                                        client_id=self.kc_manager.client_uuid,
                                        permission_id=perm_id
                                    )
                                    logger.info(f"    ✓ Deleted permission: {perm_name}")
                                except Exception as e:
                                    logger.error(f"    ✗ Failed to delete permission {perm_name}: {e}")
                                    return False

                    # Delete the resource itself - use UMA API for resource deletion
                    try:
                        self.kc_manager.uma.resource_set_delete(resource_id)
                        logger.info(f"  ✓ Deleted resource: {resource_name}")
                        deletion_success += 1
                    except Exception as e:
                        logger.error(f"  ✗ Failed to delete resource {resource_name}: {e}")
                        return False

                except Exception as e:
                    logger.error(f"Error deleting resource {resource_name}: {e}")
                    return False

            logger.info(f"Successfully deleted {deletion_success}/{len(to_delete_set)} resources")
            return deletion_success == len(to_delete_set)

        except Exception as e:
            logger.error(f"Error in bulk resource deletion: {e}")
            return False

    def ensure_default_authz(self, auth_config: Dict, ensure_default_authz: bool) -> None:
        if not ensure_default_authz:
            return

        resources = auth_config.setdefault('resources', [])
        policies = auth_config.setdefault('policies', [])

        # 1) Default Resource (represents all resources)
        default_resource_name = "Default Resource"
        has_default_resource = any(r.get('name') == default_resource_name for r in resources)
        if not has_default_resource:
            resources.append({
                "name": default_resource_name,
                "type": "urn:keycloak:resource:default",
                "ownerManagedAccess": False,
                "attributes": {},
                # Represent *all* application URLs
                "uris": ["/*"],
                "scopes": []  # no specific scopes needed for the global default
            })

        # 2) Regex-based Default Policy (instead of JS)
        default_policy_name = "Default Policy"
        has_default_policy = any(
            p.get('name') == default_policy_name and p.get('type') == 'regex'
            for p in policies
        )
        if not has_default_policy:
            policies.append({
                "name": default_policy_name,
                "type": "regex",
                "logic": "POSITIVE",
                "decisionStrategy": "UNANIMOUS",
                # In export-style JSON, provider-specific config usually lives in 'config'
                "config": {
                    # Always-match regex on a stable claim, e.g. preferred_username
                    "pattern": ".*",
                    "targetClaim": "preferred_username"
                }
            })

        # 3) Resource-based Default Permission
        default_permission_name = "Default Permission"
        has_default_permission = any(
            p.get('name') == default_permission_name and p.get('type') == 'resource'
            for p in policies
        )
        if not has_default_permission:
            policies.append({
                "name": default_permission_name,
                "type": "resource",  # resource-based permission
                "logic": "POSITIVE",
                "decisionStrategy": "AFFIRMATIVE",
                "config": {
                    # Resource-based: tie it to the Default Resource by *name*
                    "resources": f'["{default_resource_name}"]',
                    # No scopes for pure resource permission
                    "applyPolicies": f'["{default_policy_name}"]'
                }
            })

    def ensure_core_role_policies(self, auth_config: Dict) -> None:
        """
        Ensures the three core role-based policies exist in the auth_config.
        These are required for all model permissions to work.
        """
        policies = auth_config.setdefault('policies', [])

        # Define the three core role-based policies
        core_roles = ['admin', 'standard', 'view-only']

        # Check which policies already exist
        existing_policy_names = {p.get('name') for p in policies}

        for role_name in core_roles:
            full_policy_name = f"Policy - {role_name}"

            if full_policy_name not in existing_policy_names:
                # Get the role ID from Keycloak (roles must exist beforehand)
                try:
                    role = self.kc_manager.admin.get_client_role(
                        client_id=self.kc_manager.client_uuid,
                        role_name=role_name
                    )
                    role_id = role.get('id')

                    if not role_id:
                        logger.warning(f"Role '{role_name}' not found - creating it first")
                        # Create the role if it doesn't exist
                        role_payload = {
                            "name": role_name,
                            "description": f"Role for {role_name} policy",
                            "clientRole": True,
                            "composite": False,
                        }
                        self.kc_manager.admin.create_client_role(
                            client_role_id=self.kc_manager.client_uuid,
                            payload=role_payload,
                            skip_exists=True,
                        )
                        # Fetch the role again to get its ID
                        role = self.kc_manager.admin.get_client_role(
                            client_id=self.kc_manager.client_uuid,
                            role_name=role_name
                        )
                        role_id = role.get('id')

                    # Create the role-based policy in the config
                    policy = {
                        "name": full_policy_name,
                        "type": "role",
                        "logic": "POSITIVE",
                        "decisionStrategy": "UNANIMOUS",
                        "config": {
                            "roles": json.dumps([{"id": role_id, "required": True}])
                        }
                    }
                    policies.append(policy)
                    logger.info(f"   ✓ Added core policy to config: {full_policy_name}")

                except Exception as e:
                    logger.error(f"Failed to ensure policy {full_policy_name}: {e}")
            else:
                logger.info(f"   ✓ Core policy already exists: {full_policy_name}")

    def process_model_changes(self, adds: List[Tuple[str, str]], deletes: List[Tuple[str, str]], 
                            renames: List[Tuple[str, str, str]], preserve_permissions: bool = True,     ensure_default_authz: bool = False,) -> bool:
        """Process all model changes using export-operate-delete-import strategy"""

        try:
            # 1. Export current authorization settings (single export)
            logger.info("Exporting current authorization settings...")
            auth_config = self.export_configs()
            if not auth_config:
                logger.error("Failed to export authorization settings")
                return False

            # 2. Get all resources with complete data (single API call)
            logger.info("Getting all resources with complete data...")
            all_resources = self.kc_manager.admin.get_client_authz_resources(
                client_id=self.kc_manager.client_uuid
            )
            logger.info(f"Retrieved {len(all_resources)} resources with complete data")

            # Ensure required sections exist in auth_config
            if 'resources' not in auth_config:
                auth_config['resources'] = []
            if 'policies' not in auth_config:
                auth_config['policies'] = []

            logger.info(f"Auth config structure: {list(auth_config.keys())}")
            logger.info(f"Found {len(auth_config.get('resources', []))} resources in config")
            logger.info(f"Found {len(auth_config.get('policies', []))} policies in config")

            # 3. Build sets for processing
            to_delete_set = set()
            to_add_set = set()
            preserved_permissions = {}

            # Process deletions
            for app_label, model_name in deletes:
                resource_name = f"{app_label}.{model_name}"
                to_delete_set.add(resource_name)

            # Process renames - save permissions and add to delete set
            for app_label, old_name, new_name in renames:
                old_resource_name = f"{app_label}.{old_name}"
                new_resource_name = f"{app_label}.{new_name}"

                to_delete_set.add(old_resource_name)
                to_add_set.add(new_resource_name)

                if preserve_permissions:
                    # Find the old resource in the exported config
                    old_resource = None
                    for resource in auth_config['resources']:
                        if resource.get('name') == old_resource_name:
                            old_resource = resource
                            break

                    if old_resource:
                        old_permissions = self.find_permissions_for_resource_name(old_resource_name, auth_config)

                        preserved_permissions[new_resource_name] = {
                            'resource': old_resource,
                            'permissions': old_permissions
                        }
                        logger.info(f"  ✓ Preserved {len(old_permissions)} permissions for {old_resource_name} -> {new_resource_name}")
                    else:
                        logger.warning(f"  ⚠ Resource {old_resource_name} not found in config for preservation")

            # Process additions
            for app_label, model_name in adds:
                resource_name = f"{app_label}.{model_name}"
                to_add_set.add(resource_name)

            # 4. Delete resources from exported config (operate on exported data)
            resources_to_keep = []
            policies_to_keep = []


            to_delete_set_config = deepcopy(to_delete_set)
            # to_delete_set_config.add("Default Resource")


            for resource in auth_config['resources']:
                resource_name = resource.get('name')
                if resource_name not in to_delete_set_config:
                    resources_to_keep.append(resource)
                else:
                    logger.info(f"  ✓ Removed {resource_name} from config")

            # Remove permissions that reference deleted resources (permissions are in policies array)
            for policy in auth_config['policies']:
                if (True or
                        policy.get('name', '').strip() not in ["Default Policy", "Default Permission"]):
                    if policy.get('type') == 'scope':  # This is a permission
                        config = policy.get('config', {})
                        resources_str = config.get('resources', '[]')
                        try:
                            resources = json.loads(resources_str) if isinstance(resources_str, str) else resources_str
                            # Check if any referenced resource is being deleted
                            if not any(res_name in to_delete_set_config for res_name in resources):
                                policies_to_keep.append(policy)
                            else:
                                logger.info(f"  ✓ Removed permission {policy.get('name')} from config")
                        except:
                            policies_to_keep.append(policy)  # Keep if we can't parse
                    else:
                        # Regular policy, keep it
                        policies_to_keep.append(policy)

            auth_config['resources'] = resources_to_keep
            auth_config['policies'] = policies_to_keep

            # 5. Send individual deletion requests (use existing resource data - NO extra API calls)
            if to_delete_set:
                logger.info(f"Deleting {len(to_delete_set)} resources individually...")
                if not self.delete_resources_individual(to_delete_set, all_resources):
                    logger.error("Failed to delete resources individually")
                    return False
            logger.info("Ensuring core role-based policies exist before adding resources...")
            self.ensure_core_role_policies(auth_config)

            # 6. Add new resources and renamed resources to config
            if to_add_set:
                logger.info(f"Adding {len(to_add_set)} resources to config...")

                # Find policy names for standard permissions (no IDs needed)
                available_policy_names = set()
                for policy in auth_config.get('policies', []):
                    policy_name = policy.get('name')
                    if policy_name in ['Policy - admin', 'Policy - standard', 'Policy - view-only']:
                        available_policy_names.add(policy_name)

                logger.info(f"Found {len(available_policy_names)} policy names: {list(available_policy_names)}")

                # Define scope-policy mapping using policy NAMES (not IDs)
                scope_policy_mapping = {
                    'list': ['Policy - admin', 'Policy - standard', 'Policy - view-only'],
                    'read': ['Policy - admin', 'Policy - standard', 'Policy - view-only'],
                    'create': ['Policy - admin'],
                    'edit': ['Policy - admin', 'Policy - standard'],
                    'delete': ['Policy - admin'],
                    'export': ['Policy - admin', 'Policy - standard']
                }
                permissions_to_create = []

                for resource_name in to_add_set:
                    try:
                        # Check if this is a renamed model with preserved permissions
                        if resource_name in preserved_permissions:
                            # Use preserved data
                            preserved_data = preserved_permissions[resource_name]
                            old_resource = preserved_data['resource']
                            old_permissions = preserved_data['permissions']

                            # Create new resource with preserved attributes but new name (NO _id field)
                            new_resource = {
                                "name": resource_name,
                                "type": old_resource.get("type", "django-model"),
                                "ownerManagedAccess": old_resource.get("ownerManagedAccess", False),
                                "attributes": old_resource.get("attributes", {}),
                                "uris": old_resource.get("uris", []),
                                "scopes": old_resource.get("scopes", [{"name": scope} for scope in self.default_scopes])
                            }
                            auth_config['resources'].append(new_resource)

                            # Recreate permissions with new resource name in policies array
                            for old_permission in old_permissions:
                                # Update permission name to reflect new resource name
                                old_perm_name = old_permission.get('name', '')
                                old_resource_name = preserved_data['resource']['name']
                                new_perm_name = old_perm_name.replace(old_resource_name, resource_name)

                                # Convert to proper Keycloak permission format (in policies array)
                                old_config = old_permission.get('config', {})
                                new_permission = {
                                    "name": new_perm_name,
                                    "type": "scope",
                                    "logic": old_permission.get("logic", "POSITIVE"),
                                    "decisionStrategy": old_permission.get("decisionStrategy", "AFFIRMATIVE"),
                                    "config": {
                                        "resources": f'["{resource_name}"]',  # Use new resource name
                                        "scopes": old_config.get("scopes", "[]"),
                                        "applyPolicies": old_config.get("applyPolicies", "[]")
                                    }
                                }
                                auth_config['policies'].append(new_permission)

                            logger.info(f"  ✓ Added renamed resource {resource_name} with preserved permissions")

                        else:
                            # Create new resource with default permissions
                            # Check if resource already exists
                            existing_resource = None
                            for resource in auth_config['resources']:
                                if resource.get('name') == resource_name:
                                    existing_resource = resource
                                    break

                            if existing_resource:
                                logger.info(f"  ✓ Resource {resource_name} already exists in config")
                                continue

                            # Create new resource (NO _id field)
                            new_resource = {
                                "name": resource_name,
                                "ownerManagedAccess": False,
                                "attributes": {},
                                "uris": [],
                                "scopes": [{"name": scope} for scope in self.default_scopes]
                            }
                            auth_config['resources'].append(new_resource)

                            # Create default permissions for each scope (add to policies array)
                            for scope, policy_names in scope_policy_mapping.items():
                                applicable_policies = [name for name in policy_names if name in available_policy_names]
                                if applicable_policies:
                                    new_permission = {
                                        "name": f"Permission - {resource_name} - {scope}",
                                        "type": "scope",
                                        "logic": "POSITIVE",
                                        "decisionStrategy": "AFFIRMATIVE",
                                        "config": {
                                            "resources": json.dumps([resource_name]),  # Resource by name
                                            "scopes": json.dumps([scope]),  # Scope by name
                                            "applyPolicies": json.dumps(applicable_policies)  # Policy by name
                                        }
                                    }
                                    auth_config['policies'].append(new_permission)
                                    logger.info(
                                        f"   ✓ Added permission for scope '{scope}' with policies: {applicable_policies}")
                                else:
                                    logger.warning(f"No applicable policies found for scope {scope}")

                            logger.info(f"  ✓ Added new resource {resource_name} with default permissions")

                    except Exception as e:
                        logger.error(f"Error adding resource {resource_name}: {e}")
                        logger.error(f"Full traceback: {traceback.format_exc()}")
                        return False

            # After step 6 (Add new resources) and before step 7 (Import)
            # ...existing code that adds resources...

            # # 6.5. Ensure core role-based policies exist
            # logger.info("Ensuring core role-based policies exist...")
            # self.ensure_core_role_policies(auth_config)

            # Also ensure default authz if requested
            if ensure_default_authz:
                logger.info("Ensuring default authorization settings...")
                self.ensure_default_authz(auth_config, ensure_default_authz)

            # Log summary before import
            logger.info(f"Total resources to import: {len(auth_config['resources'])}")
            logger.info(f"Total policies to import: {len(auth_config['policies'])}")

            # 7. Import the updated configuration (single import)
            logger.info("Importing updated authorization settings...")
            success = self.kc_manager.import_authorization_settings(auth_config)

            # 7. Import the updated configuration (single import)
            logger.info("Importing updated authorization settings...")
            success = self.kc_manager.import_authorization_settings(auth_config)

            if success:
                logger.info("Successfully imported updated authorization settings")
                return True
            else:
                logger.error("Failed to import updated authorization settings")
                return False

        except Exception as e:
            logger.error(f"Error processing model changes: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False


class Command(BaseCommand):
    help = "Sync Keycloak authorization settings with Django model changes"

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Show what would be changed without making actual changes',
        )
        parser.add_argument(
            '--preserve-renamed-permissions',
            action='store_true',
            default=True,
            help='Preserve permissions when renaming models (default: True)',
        )
        parser.add_argument(
            '--check-missing',
            action='store_true',
            default=True,
            help='Check for Django models missing from Keycloak and add them (default: True)',
        )
        parser.add_argument(
            '--ensure-default-authz',
            action='store_true',
            default=False,
            help='Ensure a default resource, regex default policy and resource-based default permission exist'
        )

    def check_unapplied_migrations(self):
        """Check if there are unapplied migrations (migration files that exist but haven't been applied to DB)"""
        from django.db.migrations.executor import MigrationExecutor
        from django.db import connection

        executor = MigrationExecutor(connection)
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return len(plan) > 0

    def execute_migrations(self, verbosity=1, create_new=True):
        """Execute Django migrations with proper workflow"""
        try:
            success = True

            # Step 1: Check for model changes and create new migrations if needed
            if create_new:
                self.stdout.write("Creating new migrations for model changes...")
                call_command(
                    'makemigrations',
                    verbosity=verbosity,
                    interactive=False,
                    stdout=self.stdout,
                    stderr=self.stderr,
                    no_input=True
                )
                self.stdout.write("✓ New migrations created successfully")

            # Step 2: Check for unapplied migrations
            if not self.check_unapplied_migrations():
                self.stdout.write("No unapplied migrations found.")
                return success

            # Step 3: Apply unapplied migrations
            self.stdout.write("Applying unapplied migrations...")
            call_command(
                'migrate',
                verbosity=verbosity,
                interactive=False,
                stdout=self.stdout,
                stderr=self.stderr
            )
            
            
            # Execute migrations
            call_command(
                'createcachetable',
                verbosity=verbosity,
            )

            self.stdout.write("✓ Django migrations completed successfully")
            return success

        except Exception as e:
            self.stderr.write(f"✗ Migration failed: {e}")
            logger.error(f"Migration execution failed: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            return False
        

    def handle(self, *args, **options):
        tour = options.get("tour", False)
        dry_run = options.get('dry_run', False)
        preserve_permissions = options.get('preserve_renamed_permissions', True)
        check_missing = options.get('check_missing', True)
        skip_migrations = options.get('skip_migrations', False)
        migration_verbosity = options.get('migration_verbosity', 1)
        ensure_default_authz = options.get('ensure_default_authz', False)

        missing = get_missing_keycloak_env()
        if missing:
            state = str(uuid.uuid4())

            # Start minimal callback server on ephemeral port
            server, port = start_callback_server(
                state=state,
                env_file=ENV_FILE,
                state_file=STATE_FILE,
                host="127.0.0.1",
                port=0,  # auto-pick
            )

            callback_url = f"http://127.0.0.1:{port}/callback"

            try:
                url = build_instance_controller_url(state, callback_url)
            except Exception as e:
                self.stderr.write(f"Missing Keycloak env: {', '.join(missing)}")
                self.stderr.write(f"Cannot build instance-controller URL: {e}")
                return

            self.stdout.write("")
            self.stdout.write("Keycloak credentials are not configured.")
            self.stdout.write(f"Missing: {', '.join(missing)}")
            self.stdout.write("")
            self.stdout.write(f"Local callback server started on 127.0.0.1:{port}")
            self.stdout.write("Open this URL in your browser to complete setup:")
            self.stdout.write(f"  {url}")
            self.stdout.write(f"State token: {state}")
            self.stdout.write("Waiting for setup to complete (Ctrl+C to abort)...")
            self.stdout.write("")

            try:
                webbrowser.open(url)
            except Exception:
                pass

            ok = wait_for_keycloak_setup(state, timeout_seconds=900, poll_interval=3)
            if not ok:
                self.stderr.write("Init aborted; Keycloak not configured.")
                return

            self.stdout.write("Keycloak configured. Continuing Init...")

        self.stdout.write("=" * 80)
        self.stdout.write("Django Migration + Keycloak Authorization Sync")
        self.stdout.write("=" * 80)
        # Step 1: Initialize sync manager
        try:
            sync_manager = KeycloakSyncManager()
        except Exception as e:
            self.stderr.write(f"Failed to initialize Keycloak manager: {e}")
            return

        # Step 2: CRITICAL
        self.stdout.write("Detecting model changes BEFORE migrations...")

        # Detect model changes
        questioner = MigrationQuestioner(defaults={"ask_rename_model": True})
        loader = MigrationLoader(None, ignore_no_migrations=True)
        autodetector = MigrationAutodetector(
            loader.project_state(),
            ProjectState.from_apps(apps),
            questioner=questioner,
        )
        changes = autodetector.changes(graph=loader.graph)

        # Process changes
        adds, deletes, renames = [], [], []
        missing_models = {}

        for app_label, migrations in changes.items():
            for migration in migrations:
                for operation in migration.operations:
                    if isinstance(operation, CreateModel):
                        adds.append((app_label, operation.name))
                    elif isinstance(operation, DeleteModel):
                        deletes.append((app_label, operation.name))
                    elif isinstance(operation, RenameModel):
                        renames.append((app_label, operation.old_name, operation.new_name))

        # Display detected changes
        if adds or deletes or renames:
            self.stdout.write("Detected model changes:")

            for app, name in adds:
                self.stdout.write(f"  ADD {app}.{name}")

            for app, name in deletes:
                self.stdout.write(f"  DELETE {app}.{name}")

            for app, old, new in renames:
                self.stdout.write(f"  RENAME {app}.{old} -> {new}")
        else:
            self.stdout.write("No model changes detected.")
            # return

        if not skip_migrations:
            self.stdout.write("\n" + "-" * 80)
            self.stdout.write("Executing Django Migrations")
            self.stdout.write("-" * 80)

            if dry_run:
                self.stdout.write("DRY RUN: Would execute Django migrations")
                if self.check_unapplied_migrations():
                    self.stdout.write("Pending migrations found - would be executed")
                else:
                    self.stdout.write("No pending migrations found")
            else:
                if not self.execute_migrations(migration_verbosity):
                    self.stderr.write("Migration failed - aborting Keycloak sync")
                    return
        else:
            self.stdout.write("\nSkipping Django migrations (--skip-migrations)")


        if dry_run:
            self.stdout.write("Dry run mode - no changes will be made.")



        if check_missing:
            auth_config = sync_manager.export_configs()

            if auth_config:
                all_django_models = sync_manager.get_all_django_models()
                existing_keycloak_resources = sync_manager.get_existing_keycloak_resources(auth_config)
                missing_models = sync_manager.find_missing_models(all_django_models, existing_keycloak_resources, set())

                for app_name, old_name, new_name in renames:
                    if f"{app_name}.{new_name}" in missing_models:
                        missing_models.remove(f"{app_name}.{new_name}")

                if missing_models:
                    self.stdout.write(f"Would add {len(missing_models)} missing models:")
                    for model_name in sorted(missing_models):
                        self.stdout.write(f"  WOULD ADD: {model_name}")
                else:
                    self.stdout.write("No missing models found.")

        if dry_run:
            return

        # Process all changes using the optimized strategy
        self.stdout.write("\nSyncing changes to Keycloak...")
        missing_models = {tuple(model.split(".")) for model in missing_models}
        missing_models.union(set(adds))
        new_missing_models_list = list(missing_models)

        success = sync_manager.process_model_changes(new_missing_models_list, deletes, renames, preserve_permissions, ensure_default_authz=ensure_default_authz,
)

        if success:
            self.stdout.write("✓ All model changes successfully synced to Keycloak!")
        else:
            self.stdout.write("✗ Some operations failed. Check logs for details.")