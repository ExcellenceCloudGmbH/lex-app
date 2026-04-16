from __future__ import annotations

# core/management/commands/init.py

import argparse
import asyncio
import json
import logging
import shlex
import socket
import threading
import time
import traceback
import uuid
import webbrowser
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Set, Tuple
from urllib.parse import urlencode

import requests
import uvicorn
from django.apps import apps
from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import DEFAULT_DB_ALIAS, connections
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.operations.models import CreateModel, DeleteModel, RenameModel
from django.db.migrations.questioner import MigrationQuestioner
from django.db.migrations.state import ProjectState

from lex.core.management.commands.bootstrap_callback_server import start_callback_server
from lex.runtime_config import format_db_connection_unicode_error

logger = logging.getLogger(__name__)

KEYCLOAK_ENV_VARS = [
    "KEYCLOAK_URL",
    "KEYCLOAK_REALM",
    "KEYCLOAK_REALM_NAME",
    "OIDC_RP_CLIENT_ID",
    "OIDC_RP_CLIENT_SECRET",
    "OIDC_RP_CLIENT_UUID",
]

DEFAULT_CLIENT_ROLES = ("admin", "standard", "view-only")
IGNORED_CLIENT_ROLES = frozenset({"manage-client", "uma_protection"})
DEFAULT_SCOPE_POLICY_MAPPING = {
    "list": ["Policy - admin", "Policy - standard", "Policy - view-only"],
    "read": ["Policy - admin", "Policy - standard", "Policy - view-only"],
    "create": ["Policy - admin"],
    "edit": ["Policy - admin", "Policy - standard"],
    "delete": ["Policy - admin"],
    "export": ["Policy - admin", "Policy - standard"],
}
NON_FATAL_KEYCLOAK_IMPORT_ERROR_KINDS = frozenset({"timeout", "gateway_timeout"})
KEYCLOAK_SYNC_EXCLUDED_APPS = frozenset({"legacy_data"})
KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES = frozenset(
    {
        "audit_logging.AuditLog",
        "audit_logging.AuditLogStatus",
        "audit_logging.CalculationLog",
    }
)
KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES = ("historical", "metahistorical")

STATE_FILE = Path(
    getattr(
        settings,
        "KEYCLOAK_STATE_FILE",
        Path((Path.cwd() if str(Path.cwd()) else settings.BASE_DIR)) / ".keycloak_state.json",
    )
)
ENV_FILE = Path((Path.cwd() if str(Path.cwd()) else settings.BASE_DIR)) / ".env"


def _get_keycloak_import_error_details(sync_manager: "KeycloakSyncManager") -> dict[str, Any] | None:
    error = getattr(getattr(sync_manager, "kc_manager", None), "last_authz_import_error", None)
    return error if isinstance(error, dict) else None


def _format_keycloak_import_error_details(error: dict[str, Any] | None) -> str | None:
    if not error:
        return None

    kind = error.get("kind")
    response_text = error.get("response_text")
    if kind == "timeout":
        timeout = error.get("timeout")
        return f"request timed out after {timeout}" if timeout is not None else "request timed out"
    if kind == "gateway_timeout":
        status_code = error.get("status_code", 504)
        return (
            f"Keycloak returned HTTP {status_code}: {response_text}"
            if response_text
            else f"Keycloak returned HTTP {status_code}"
        )
    if kind == "http_error":
        status_code = error.get("status_code")
        if status_code is None:
            return response_text or None
        return (
            f"Keycloak returned HTTP {status_code}: {response_text}"
            if response_text
            else f"Keycloak returned HTTP {status_code}"
        )
    return None


def _is_non_fatal_keycloak_import_timeout(sync_manager: "KeycloakSyncManager") -> bool:
    error = _get_keycloak_import_error_details(sync_manager)
    return bool(error and error.get("kind") in NON_FATAL_KEYCLOAK_IMPORT_ERROR_KINDS)


def get_missing_keycloak_env() -> list[str]:
    missing: list[str] = []
    for key in KEYCLOAK_ENV_VARS:
        if key in ("KEYCLOAK_REALM", "KEYCLOAK_REALM_NAME"):
            if not (
                settings.__dict__.get("KEYCLOAK_REALM_NAME")
                or settings.__dict__.get("KEYCLOAK_REALM")
                or (settings.__dict__.get("KEYCLOAK_REALM_NAME") is not None)
                or (settings.__dict__.get("KEYCLOAK_REALM") is not None)
                or (settings.__dict__.get("KEYCLOAK_REALM_NAME") is not None)
            ) and not (
                # env var presence
                bool(__import__("os").environ.get("KEYCLOAK_REALM"))
                or bool(__import__("os").environ.get("KEYCLOAK_REALM_NAME"))
                or bool(getattr(settings, "KEYCLOAK_REALM_NAME", None))
            ):
                missing.append("KEYCLOAK_REALM/KEYCLOAK_REALM_NAME")
            continue

        import os as _os

        if not (_os.getenv(key) or getattr(settings, key, None)):
            missing.append(key)
    return missing


def build_instance_controller_url(state: str, callback_url: str) -> str:
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    if not base:
        raise CommandError("INSTANCE_CONTROLLER_BASE_URL is not configured")
    params = {
        "state": state,
        "callback": callback_url,
        "flow": "keycloak-client-bootstrap",
        "project": getattr(settings, "LEX_PROJECT_SLUG", "lex-app"),
    }
    return f"{base}/lex/keycloak-bootstrap?{urlencode(params)}"


def _load_state_map() -> dict:
    """
    Fail-fast: corrupted JSON or IO errors are fatal.
    """
    if not STATE_FILE.exists():
        return {}
    try:
        raw = STATE_FILE.read_text(encoding="utf-8") or "{}"
    except Exception as e:
        raise CommandError(f"Failed to read state file {STATE_FILE}: {e}") from e
    try:
        return json.loads(raw)
    except Exception as e:
        raise CommandError(f"State file {STATE_FILE} contains invalid JSON: {e}") from e


def _save_state_map(data: dict) -> None:
    tmp = STATE_FILE.with_suffix(".tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except Exception as e:
        raise CommandError(f"Failed to write state file {STATE_FILE}: {e}") from e


def get_state(state: str) -> str | None:
    data = _load_state_map()
    return data.get(state)


def set_state(state: str, value: str) -> None:
    data = _load_state_map()
    data[state] = value
    _save_state_map(data)


def wait_for_keycloak_setup(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> None:
    """
    Fail-fast: any unexpected error aborts; timeouts/cancel are fatal to init.
    """
    start = time.time()
    while True:
        if not get_missing_keycloak_env():
            print("Keycloak credentials detected; continuing init.")
            return

        current = get_state(state)
        if current == "done":
            print("Keycloak setup completed; continuing init.")
            return
        if current == "cancelled":
            raise CommandError("Keycloak setup cancelled; stopping init.")

        if time.time() - start > timeout_seconds:
            raise CommandError(f"Timed out waiting for Keycloak setup after {timeout_seconds} seconds.")

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


def poll_bootstrap_status(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> dict:
    """
    Strict polling: request/JSON/HTTP errors are fatal (no silent retry).
    """
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    if not base:
        raise CommandError("INSTANCE_CONTROLLER_BASE_URL not configured")

    url = f"{base}/api/lex/bootstrap_status/{state}"
    start = time.time()

    while True:
        if time.time() - start > timeout_seconds:
            raise CommandError(f"Timed out waiting for Keycloak setup after {timeout_seconds} seconds.")

        resp = requests.get(url, timeout=10)
        try:
            resp.raise_for_status()
        except Exception as e:
            raise CommandError(f"Error polling bootstrap status ({resp.status_code}): {resp.text}") from e

        try:
            data = resp.json()
        except Exception as e:
            raise CommandError(f"bootstrap_status returned non-JSON response: {resp.text}") from e

        status_val = data.get("status")
        if status_val == "done":
            payload = data.get("payload")
            if not payload:
                raise CommandError("bootstrap_status is 'done' but payload is missing")
            print("Keycloak credentials retrieved from instance-controller.")
            return payload
        if status_val == "cancelled":
            raise CommandError("Keycloak setup cancelled.")

        time.sleep(poll_interval)


def update_env_file(values: dict) -> None:
    """
    Fail-fast: IO errors abort.
    Also fixes temp file naming for hidden .env files.
    """
    env_file = Path(settings.BASE_DIR) / ".env"
    text = ""
    if env_file.exists():
        try:
            text = env_file.read_text(encoding="utf-8")
        except Exception as e:
            raise CommandError(f"Failed to read env file {env_file}: {e}") from e

    lines = text.splitlines()
    kv: dict[str, str] = {}
    non_kv_lines: list[str] = []

    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            non_kv_lines.append(line)
            continue
        k, v = stripped.split("=", 1)
        kv[k] = v

    for k, v in values.items():
        kv[k] = str(v)

    out_lines: list[str] = []
    out_lines.extend(non_kv_lines)
    for k, v in kv.items():
        out_lines.append(f"{k}={v}")

    tmp = env_file.parent / f"{env_file.name}.tmp"  # safe for ".env"
    try:
        tmp.write_text("\n".join(out_lines) + "\n", encoding="utf-8")
        tmp.replace(env_file)
    except Exception as e:
        raise CommandError(f"Failed to write env file {env_file}: {e}") from e


def fetch_bootstrap_state(state: str) -> dict | None:
    base = getattr(settings, "INSTANCE_CONTROLLER_BASE_URL", "").rstrip("/")
    if not base:
        raise CommandError("INSTANCE_CONTROLLER_BASE_URL not configured")

    url = f"{base}/api/lex/bootstrap_status/{state}"
    resp = requests.get(url, timeout=5)
    try:
        resp.raise_for_status()
    except Exception as e:
        raise CommandError(f"Error fetching bootstrap state ({resp.status_code}): {resp.text}") from e

    try:
        data = resp.json()
    except Exception as e:
        raise CommandError(f"bootstrap_status returned non-JSON response: {resp.text}") from e

    if data.get("status") == "done" and data.get("payload"):
        return data["payload"]
    if data.get("status") == "cancelled":
        return {"cancelled": True}
    return None


def wait_for_keycloak_setup_v2(state: str, timeout_seconds: int = 900, poll_interval: int = 3) -> dict:
    """
    Strict: request/JSON errors are fatal; timeout/cancel is fatal.
    """
    import os

    start = time.time()
    while True:
        if not get_missing_keycloak_env():
            return {
                "KEYCLOAK_URL": os.getenv("KEYCLOAK_URL", ""),
                "KEYCLOAK_REALM": os.getenv("KEYCLOAK_REALM", "") or os.getenv("KEYCLOAK_REALM_NAME", ""),
                "OIDC_RP_CLIENT_ID": os.getenv("OIDC_RP_CLIENT_ID", ""),
                "OIDC_RP_CLIENT_SECRET": os.getenv("OIDC_RP_CLIENT_SECRET", ""),
                "OIDC_RP_CLIENT_UUID": os.getenv("OIDC_RP_CLIENT_UUID", ""),
            }

        state_data = fetch_bootstrap_state(state)
        if state_data:
            if state_data.get("cancelled"):
                raise CommandError("Keycloak setup cancelled.")
            return state_data

        if time.time() - start > timeout_seconds:
            raise CommandError(f"Timed out waiting for Keycloak setup after {timeout_seconds} seconds.")

        time.sleep(poll_interval)


class KeycloakSyncManager:
    """Handles the sync between Django models and Keycloak resources/permissions"""

    def __init__(self):
        from lex.api.views.authentication.KeycloakManager import KeycloakManager
        from dotenv import dotenv_values
        import os

        env_config = dotenv_values(str(ENV_FILE))
        for key in KEYCLOAK_ENV_VARS:
            if key in env_config and env_config[key] is not None:
                os.environ[key] = str(env_config[key])

        self.kc_manager = KeycloakManager()
        self.default_scopes = ["list", "read", "create", "edit", "delete", "export"]
        self.exported_configs = None

    def _snapshot_client_settings(self) -> dict:
        client_id = self.kc_manager.client_uuid
        rep = self.kc_manager.admin.get_client(client_id)

        keep = [
            "authorizationServicesEnabled",
            "redirectUris",
            "webOrigins",
            "authenticationFlowBindingOverrides",
            "protocol",
            "publicClient",
            "serviceAccountsEnabled",
            "standardFlowEnabled",
            "directAccessGrantsEnabled",
        ]
        return {k: deepcopy(rep.get(k)) for k in keep if k in rep}

    def _restore_client_settings(self, snapshot: dict) -> None:
        client_id = self.kc_manager.client_uuid
        rep = self.kc_manager.admin.get_client(client_id)

        changed = False
        for k, v in snapshot.items():
            if rep.get(k) != v:
                rep[k] = v
                changed = True

        if changed:
            self.kc_manager.admin.update_client(client_id, rep)

    @staticmethod
    def _resource_name(app_label: str, model_name: str) -> str:
        return f"{app_label}.{model_name}"

    @classmethod
    def is_keycloak_sync_excluded_model(cls, app_label: str, model_name: str) -> bool:
        if not model_name:
            return False

        if app_label in KEYCLOAK_SYNC_EXCLUDED_APPS:
            return True

        resource_name = cls._resource_name(app_label, model_name)
        if resource_name in KEYCLOAK_SYNC_EXCLUDED_RESOURCE_NAMES:
            return True

        return model_name.lower().startswith(KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES)

    @classmethod
    def is_keycloak_sync_excluded_resource_name(cls, resource_name: str | None) -> bool:
        if not resource_name:
            return False

        if "." not in resource_name:
            return resource_name.lower().startswith(KEYCLOAK_SYNC_EXCLUDED_MODEL_PREFIXES)

        app_label, model_name = resource_name.split(".", 1)
        return cls.is_keycloak_sync_excluded_model(app_label, model_name)

    def get_all_django_models(self) -> Set[str]:
        """
        Fail-fast: do NOT swallow per-app errors.
        """
        all_models: Set[str] = set()
        repo_name = settings.repo_name if hasattr(settings, "repo_name") else None

        skip_apps = {
            "admin",
            "auth",
            "contenttypes",
            "sessions",
            "messages",
            "staticfiles",
            "migrations",
            "django_extensions",
            "lex_app"
        }

        for app_config in apps.get_app_configs():
            app_label = app_config.label

            if not repo_name:
                if app_label in skip_apps:
                    logger.debug(f"Skipping built-in app: {app_label}")
                    continue
            else:
                logger.debug(f"Using repo_name: {repo_name}")
                if repo_name != app_label:
                    continue

            for model in app_config.get_models():
                if model._meta.abstract:
                    continue
                if model._meta.proxy:
                    continue

                model_name = model.__name__
                if self.is_keycloak_sync_excluded_model(app_label, model_name):
                    logger.debug(
                        "Skipping Keycloak sync for immutable model: %s",
                        self._resource_name(app_label, model_name),
                    )
                    continue

                all_models.add(self._resource_name(app_label, model_name))

        logger.info(f"Found {len(all_models)} Django models total")
        return all_models

    def export_configs(self):
        if self.exported_configs:
            return self.exported_configs
        cfg = self.kc_manager.export_authorization_settings()
        if not cfg:
            raise Exception("Failed to export Keycloak authorization settings (empty/None).")
        self.exported_configs = cfg
        return cfg

    def get_existing_keycloak_resources(self, auth_config: Dict) -> Set[str]:
        existing_resources: Set[str] = set()
        for resource in auth_config.get("resources", []):
            resource_name = resource.get("name")
            if resource_name:
                existing_resources.add(resource_name)
        logger.info(f"Found {len(existing_resources)} existing Keycloak resources")
        return existing_resources

    def find_missing_models(
        self,
        all_django_models: Set[str],
        existing_keycloak_resources: Set[str],
        to_delete_set: Set[str],
    ) -> Set[str]:
        missing_models = all_django_models - existing_keycloak_resources - to_delete_set
        if missing_models:
            logger.info(f"Found {len(missing_models)} models missing from Keycloak.")
        else:
            logger.info("No missing models found - all Django models are synced with Keycloak")
        return missing_models

    @staticmethod
    def _parse_json_maybe(value: Any, context: str) -> list:
        if isinstance(value, list):
            return value
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except Exception as e:
                raise CommandError(f"Failed to parse JSON for {context}: {value!r}") from e
            if not isinstance(parsed, list):
                raise CommandError(f"Expected a JSON list for {context}, got: {type(parsed).__name__}")
            return parsed
        raise CommandError(f"Unexpected type for {context}: {type(value).__name__}")

    def find_permissions_for_resource_name(self, resource_name: str, auth_config: Dict) -> List[Dict[str, Any]]:
        resource_permissions: List[Dict[str, Any]] = []
        for policy in auth_config.get("policies", []):
            if policy.get("type") == "scope":
                config = policy.get("config", {})
                resources_val = config.get("resources", "[]")
                resources = self._parse_json_maybe(resources_val, f"policy.config.resources for {policy.get('name')}")
                if resource_name in resources:
                    resource_permissions.append(policy)
        return resource_permissions

    def delete_resources_individual(self, to_delete_set: Set[str], all_resources: List[Dict]) -> None:
        """
        Fail-fast: any delete failure raises.
        """
        logger.info(f"Using existing resource data with {len(all_resources)} resources")

        resource_name_to_id: Dict[str, str] = {}
        for resource in all_resources:
            resource_name = resource.get("name")
            if resource_name in to_delete_set:
                resource_id = resource.get("_id")
                if resource_id:
                    resource_name_to_id[resource_name] = resource_id
                    logger.info(f"Found resource to delete: {resource_name} -> {resource_id}")

        logger.info(f"Found {len(resource_name_to_id)} resources to delete")

        # Fetch permissions once (fail-fast if API fails)
        permissions = self.kc_manager.admin.get_client_authz_permissions(
            client_id=self.kc_manager.client_uuid
        )

        for resource_name in to_delete_set:
            if resource_name not in resource_name_to_id:
                logger.warning(f"Resource {resource_name} not found, may already be deleted")
                continue

            resource_id = resource_name_to_id[resource_name]

            # delete permissions that reference this resource
            for permission in permissions:
                perm_resources = permission.get("resources", [])
                if resource_id in perm_resources:
                    perm_id = permission.get("id")
                    perm_name = permission.get("name")
                    if not perm_id:
                        raise CommandError(f"Permission has no id: {permission}")
                    self.kc_manager.admin.delete_client_authz_permission(
                        client_id=self.kc_manager.client_uuid,
                        permission_id=perm_id,
                    )
                    logger.info(f"    ✓ Deleted permission: {perm_name}")

            # delete the resource
            self.kc_manager.uma.resource_set_delete(resource_id)
            logger.info(f"  ✓ Deleted resource: {resource_name}")

    def ensure_default_authz(self, auth_config: Dict, ensure_default_authz: bool) -> None:
        if not ensure_default_authz:
            return

        resources = auth_config.setdefault("resources", [])
        policies = auth_config.setdefault("policies", [])

        default_resource_name = "Default Resource"
        if not any(r.get("name") == default_resource_name for r in resources):
            resources.append(
                {
                    "name": default_resource_name,
                    "type": "urn:keycloak:resource:default",
                    "ownerManagedAccess": False,
                    "attributes": {},
                    "uris": ["/*"],
                    "scopes": [],
                }
            )

        default_policy_name = "Default Policy"
        if not any(p.get("name") == default_policy_name and p.get("type") == "regex" for p in policies):
            policies.append(
                {
                    "name": default_policy_name,
                    "type": "regex",
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "config": {"pattern": ".*", "targetClaim": "preferred_username"},
                }
            )

        default_permission_name = "Default Permission"
        if not any(p.get("name") == default_permission_name and p.get("type") == "resource" for p in policies):
            policies.append(
                {
                    "name": default_permission_name,
                    "type": "resource",
                    "logic": "POSITIVE",
                    "decisionStrategy": "AFFIRMATIVE",
                    "config": {
                        "resources": f'["{default_resource_name}"]',
                        "applyPolicies": f'["{default_policy_name}"]',
                    },
                }
            )

    def _ordered_client_role_names(self, role_names: Set[str]) -> List[str]:
        ordered = [role_name for role_name in DEFAULT_CLIENT_ROLES if role_name in role_names]
        ordered.extend(sorted(role_name for role_name in role_names if role_name not in DEFAULT_CLIENT_ROLES))
        return ordered

    def normalize_role_policy_references(self, auth_config: Dict, client_roles: Dict[str, Dict[str, Any]]) -> None:
        policies = auth_config.get("policies", [])
        if not isinstance(policies, list):
            raise CommandError("auth_config.policies must be a list")

        normalized_count = 0
        for policy in policies:
            if not isinstance(policy, dict) or policy.get("type") != "role":
                continue

            config = policy.get("config")
            if not isinstance(config, dict):
                config = {}
                policy["config"] = config

            desired_role_name = None
            policy_name = policy.get("name")
            if isinstance(policy_name, str) and policy_name.startswith("Policy - "):
                candidate = policy_name.removeprefix("Policy - ").strip()
                if candidate in client_roles:
                    desired_role_name = candidate

            raw_roles = policy.get("roles")
            if raw_roles is None:
                raw_roles = config.get("roles")
            if raw_roles is None:
                continue

            parsed_roles = self._parse_json_maybe(
                raw_roles,
                f"role policy roles for {policy.get('name')}",
            )

            normalized_roles = []
            for raw_role in parsed_roles:
                if not isinstance(raw_role, dict):
                    raise CommandError(
                        f"Unexpected role entry type for policy {policy.get('name')}: {type(raw_role).__name__}"
                    )

                role_name = raw_role.get("name")
                if not role_name:
                    role_id = raw_role.get("id")
                    if isinstance(role_id, str) and role_id in {role.get("id") for role in client_roles.values()}:
                        role_name = next(
                            (name for name, role in client_roles.items() if role.get("id") == role_id),
                            None,
                        )
                    elif isinstance(role_id, str) and "/" in role_id:
                        role_name = role_id.rsplit("/", 1)[-1]

                if desired_role_name and desired_role_name in client_roles:
                    role_name = desired_role_name

                if not role_name or role_name not in client_roles:
                    normalized_roles.append(raw_role)
                    continue

                normalized_roles.append(
                    {
                        "id": client_roles[role_name]["id"],
                        "required": bool(raw_role.get("required", True)),
                    }
                )

            canonical_roles_json = json.dumps(normalized_roles, separators=(",", ":"))
            if "roles" in policy:
                policy.pop("roles", None)
                normalized_count += 1
            if config.get("roles") != canonical_roles_json:
                config["roles"] = canonical_roles_json
                normalized_count += 1

        if normalized_count:
            logger.info("   ✓ Normalized %s role policy reference field(s) before import", normalized_count)

    def get_client_roles(self) -> Dict[str, Dict[str, Any]]:
        client_roles = {
            role["name"]: role
            for role in self.kc_manager.admin.get_client_roles(client_id=self.kc_manager.client_uuid)
            if role.get("name") and role["name"] not in IGNORED_CLIENT_ROLES
        }

        for role_name in DEFAULT_CLIENT_ROLES:
            if role_name in client_roles:
                continue

            logger.warning(f"Default role '{role_name}' not found - creating it first")
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
            role = self.kc_manager.admin.get_client_role(
                client_id=self.kc_manager.client_uuid, role_name=role_name
            )
            role_id = role.get("id")
            if not role_id:
                raise CommandError(f"Failed to create/fetch client role '{role_name}'")
            client_roles[role_name] = role

        return client_roles

    def build_scope_policy_mapping(self, role_names: List[str]) -> Dict[str, List[str]]:
        scope_policy_mapping = {
            scope: list(policy_names) for scope, policy_names in DEFAULT_SCOPE_POLICY_MAPPING.items()
        }
        extra_policy_names = [
            f"Policy - {role_name}" for role_name in role_names if role_name not in DEFAULT_CLIENT_ROLES
        ]

        if extra_policy_names:
            for scope, policy_names in scope_policy_mapping.items():
                if "Policy - standard" not in policy_names:
                    continue

                updated_policy_names = []
                for policy_name in policy_names:
                    if policy_name not in updated_policy_names:
                        updated_policy_names.append(policy_name)
                    if policy_name == "Policy - standard":
                        for extra_policy_name in extra_policy_names:
                            if extra_policy_name not in updated_policy_names:
                                updated_policy_names.append(extra_policy_name)
                scope_policy_mapping[scope] = updated_policy_names

        return scope_policy_mapping

    def sync_standard_client_role_permissions(self, auth_config: Dict, role_names: List[str]) -> None:
        extra_policy_names = [
            f"Policy - {role_name}" for role_name in role_names if role_name not in DEFAULT_CLIENT_ROLES
        ]
        if not extra_policy_names:
            return

        available_policy_names = {p.get("name") for p in auth_config.get("policies", []) if p.get("name")}
        extra_policy_names = [policy_name for policy_name in extra_policy_names if policy_name in available_policy_names]
        if not extra_policy_names:
            return

        updated_permissions = 0
        for policy in auth_config.get("policies", []):
            config = policy.get("config")
            if not isinstance(config, dict) or "applyPolicies" not in config:
                continue

            apply_policy_names = self._parse_json_maybe(
                config.get("applyPolicies", "[]"),
                f"policy.config.applyPolicies for {policy.get('name')}",
            )
            if "Policy - standard" not in apply_policy_names:
                continue

            updated_apply_policy_names = []
            for policy_name in apply_policy_names:
                if policy_name not in updated_apply_policy_names:
                    updated_apply_policy_names.append(policy_name)
                if policy_name == "Policy - standard":
                    for extra_policy_name in extra_policy_names:
                        if extra_policy_name not in updated_apply_policy_names:
                            updated_apply_policy_names.append(extra_policy_name)

            if updated_apply_policy_names == apply_policy_names:
                continue

            config["applyPolicies"] = json.dumps(updated_apply_policy_names)
            updated_permissions += 1

        if updated_permissions:
            logger.info(
                "   ✓ Updated %s permission(s) to inherit the standard-role configuration",
                updated_permissions,
            )

    def ensure_client_role_policies(self, auth_config: Dict) -> List[str]:
        """
        Fail-fast: any Keycloak admin API error raises.
        """
        policies = auth_config.setdefault("policies", [])
        policies_by_name = {p.get("name"): p for p in policies if p.get("name")}
        client_roles = self.get_client_roles()
        ordered_role_names = self._ordered_client_role_names(set(client_roles))

        for role_name in ordered_role_names:
            full_policy_name = f"Policy - {role_name}"
            role = client_roles[role_name]
            role_id = role.get("id")
            if not role_id:
                raise CommandError(f"Client role '{role_name}' is missing an id")
            role_definitions = [{"id": role_id, "required": True}]
            canonical_roles_json = json.dumps(role_definitions, separators=(",", ":"))

            existing_policy = policies_by_name.get(full_policy_name)
            if existing_policy:
                updated = False
                if existing_policy.get("type") != "role":
                    existing_policy["type"] = "role"
                    updated = True
                if existing_policy.get("logic") != "POSITIVE":
                    existing_policy["logic"] = "POSITIVE"
                    updated = True
                if existing_policy.get("decisionStrategy") != "UNANIMOUS":
                    existing_policy["decisionStrategy"] = "UNANIMOUS"
                    updated = True

                config = existing_policy.get("config")
                if not isinstance(config, dict):
                    config = {}
                    existing_policy["config"] = config
                    updated = True

                if config.get("roles") != canonical_roles_json:
                    config["roles"] = canonical_roles_json
                    updated = True
                if "roles" in existing_policy:
                    existing_policy.pop("roles", None)
                    updated = True

                if updated:
                    logger.info(f"   ✓ Normalized client role policy in config: {full_policy_name}")
                else:
                    logger.info(f"   ✓ Client role policy already exists: {full_policy_name}")
                continue

            policies.append(
                {
                    "name": full_policy_name,
                    "type": "role",
                    "logic": "POSITIVE",
                    "decisionStrategy": "UNANIMOUS",
                    "config": {"roles": canonical_roles_json},
                }
            )
            policies_by_name[full_policy_name] = policies[-1]
            logger.info(f"   ✓ Added client role policy to config: {full_policy_name}")

        return ordered_role_names

    def process_model_changes(
        self,
        adds: List[Tuple[str, str]],
        deletes: List[Tuple[str, str]],
        renames: List[Tuple[str, str, str]],
        preserve_permissions: bool = True,
        ensure_default_authz: bool = False,
    ) -> None:
        """
        Fail-fast:
          - Any error raises (no silent return False)
          - Always attempts to restore client snapshot in finally
        """
        self.kc_manager.last_authz_import_error = None
        client_snapshot = self._snapshot_client_settings()
        had_primary_error: Exception | None = None

        try:
            logger.info("Exporting current authorization settings...")
            auth_config = self.export_configs()

            logger.info("Getting all resources with complete data...")
            all_resources = self.kc_manager.admin.get_client_authz_resources(
                client_id=self.kc_manager.client_uuid
            )
            logger.info(f"Retrieved {len(all_resources)} resources with complete data")

            auth_config.setdefault("resources", [])
            auth_config.setdefault("policies", [])

            to_delete_set: Set[str] = set()
            to_add_set: Set[str] = set()
            preserved_permissions: Dict[str, Dict[str, Any]] = {}

            for app_label, model_name in deletes:
                to_delete_set.add(self._resource_name(app_label, model_name))

            for app_label, old_name, new_name in renames:
                old_resource_name = self._resource_name(app_label, old_name)
                new_resource_name = self._resource_name(app_label, new_name)
                old_resource_is_excluded = self.is_keycloak_sync_excluded_resource_name(old_resource_name)
                new_resource_is_excluded = self.is_keycloak_sync_excluded_resource_name(new_resource_name)

                to_delete_set.add(old_resource_name)

                if new_resource_is_excluded:
                    logger.info(
                        "  ✓ Skipping immutable resource target during rename sync: %s",
                        new_resource_name,
                    )
                    continue

                to_add_set.add(new_resource_name)

                if preserve_permissions and not old_resource_is_excluded:
                    old_resource = next(
                        (r for r in auth_config["resources"] if r.get("name") == old_resource_name), None
                    )
                    if not old_resource:
                        raise CommandError(
                            f"Rename preservation requested but old resource not found in export: {old_resource_name}"
                        )
                    old_permissions = self.find_permissions_for_resource_name(old_resource_name, auth_config)
                    preserved_permissions[new_resource_name] = {
                        "resource": old_resource,
                        "permissions": old_permissions,
                    }
                    logger.info(
                        f"  ✓ Preserved {len(old_permissions)} permissions for {old_resource_name} -> {new_resource_name}"
                    )

            for app_label, model_name in adds:
                resource_name = self._resource_name(app_label, model_name)
                if self.is_keycloak_sync_excluded_resource_name(resource_name):
                    logger.info("  ✓ Skipping immutable resource sync: %s", resource_name)
                    continue
                to_add_set.add(resource_name)

            existing_excluded_resources: Set[str] = set()
            for resource in auth_config["resources"]:
                resource_name = resource.get("name")
                if self.is_keycloak_sync_excluded_resource_name(resource_name):
                    existing_excluded_resources.add(resource_name)

            for resource in all_resources:
                resource_name = resource.get("name")
                if self.is_keycloak_sync_excluded_resource_name(resource_name):
                    existing_excluded_resources.add(resource_name)

            if existing_excluded_resources:
                logger.info(
                    "Pruning %s immutable resource(s) from Keycloak sync: %s",
                    len(existing_excluded_resources),
                    ", ".join(sorted(existing_excluded_resources)),
                )
                to_delete_set.update(existing_excluded_resources)

            # remove deleted resources from config
            resources_to_keep = []
            for resource in auth_config["resources"]:
                name = resource.get("name")
                if name in to_delete_set:
                    logger.info(f"  ✓ Removed {name} from config")
                else:
                    resources_to_keep.append(resource)

            policies_to_keep = []
            for policy in auth_config["policies"]:
                if policy.get("type") == "scope":
                    cfg = policy.get("config", {})
                    resources_val = cfg.get("resources", "[]")
                    resources = self._parse_json_maybe(
                        resources_val, f"policy.config.resources for {policy.get('name')}"
                    )
                    if any(res_name in to_delete_set for res_name in resources):
                        logger.info(f"  ✓ Removed permission {policy.get('name')} from config")
                        continue
                policies_to_keep.append(policy)

            auth_config["resources"] = resources_to_keep
            auth_config["policies"] = policies_to_keep

            # delete in keycloak
            if to_delete_set:
                logger.info(f"Deleting {len(to_delete_set)} resources individually...")
                self.delete_resources_individual(to_delete_set, all_resources)

            logger.info("Ensuring client role-based policies exist before adding resources...")
            managed_role_names = self.ensure_client_role_policies(auth_config)
            client_roles = self.get_client_roles()
            self.normalize_role_policy_references(auth_config, client_roles)

            # add resources + permissions
            if to_add_set:
                managed_policy_names = {f"Policy - {role_name}" for role_name in managed_role_names}
                available_policy_names = {
                    p.get("name")
                    for p in auth_config.get("policies", [])
                    if p.get("name") in managed_policy_names
                }
                scope_policy_mapping = self.build_scope_policy_mapping(managed_role_names)

                for resource_name in to_add_set:
                    if any(r.get("name") == resource_name for r in auth_config["resources"]):
                        logger.info(f"  ✓ Resource {resource_name} already exists in config")
                        continue

                    if resource_name in preserved_permissions:
                        preserved = preserved_permissions[resource_name]
                        old_resource = preserved["resource"]
                        old_permissions = preserved["permissions"]

                        new_resource = {
                            "name": resource_name,
                            "type": old_resource.get("type", "django-model"),
                            "ownerManagedAccess": old_resource.get("ownerManagedAccess", False),
                            "attributes": old_resource.get("attributes", {}),
                            "uris": old_resource.get("uris", []),
                            "scopes": old_resource.get(
                                "scopes", [{"name": scope} for scope in self.default_scopes]
                            ),
                        }
                        auth_config["resources"].append(new_resource)

                        for old_permission in old_permissions:
                            old_perm_name = old_permission.get("name", "")
                            old_resource_name = old_resource.get("name", "")
                            new_perm_name = old_perm_name.replace(old_resource_name, resource_name)

                            old_config = old_permission.get("config", {})
                            new_permission = {
                                "name": new_perm_name,
                                "type": "scope",
                                "logic": old_permission.get("logic", "POSITIVE"),
                                "decisionStrategy": old_permission.get(
                                    "decisionStrategy", "AFFIRMATIVE"
                                ),
                                "config": {
                                    "resources": json.dumps([resource_name]),
                                    "scopes": old_config.get("scopes", "[]"),
                                    "applyPolicies": old_config.get("applyPolicies", "[]"),
                                },
                            }
                            auth_config["policies"].append(new_permission)

                        logger.info(f"  ✓ Added renamed resource {resource_name} with preserved permissions")
                        continue

                    # brand new resource
                    new_resource = {
                        "name": resource_name,
                        "ownerManagedAccess": False,
                        "attributes": {},
                        "uris": [],
                        "scopes": [{"name": scope} for scope in self.default_scopes],
                    }
                    auth_config["resources"].append(new_resource)

                    for scope, policy_names in scope_policy_mapping.items():
                        applicable = [p for p in policy_names if p in available_policy_names]
                        if not applicable:
                            raise CommandError(
                                f"No applicable policies found for scope {scope}. "
                                f"Expected core policies missing? available={sorted(available_policy_names)}"
                            )
                        auth_config["policies"].append(
                            {
                                "name": f"Permission - {resource_name} - {scope}",
                                "type": "scope",
                                "logic": "POSITIVE",
                                "decisionStrategy": "AFFIRMATIVE",
                                "config": {
                                    "resources": json.dumps([resource_name]),
                                    "scopes": json.dumps([scope]),
                                    "applyPolicies": json.dumps(applicable),
                                },
                            }
                        )

                    logger.info(f"  ✓ Added new resource {resource_name} with default permissions")

            self.sync_standard_client_role_permissions(auth_config, managed_role_names)

            if ensure_default_authz:
                logger.info("Ensuring default authorization settings...")
                self.ensure_default_authz(auth_config, ensure_default_authz)

            logger.info(f"Total resources to import: {len(auth_config['resources'])}")
            logger.info(f"Total policies to import: {len(auth_config['policies'])}")

            logger.info("Importing updated authorization settings...")
            success = self.kc_manager.import_authorization_settings(auth_config)
            if not success:
                error_details = _format_keycloak_import_error_details(
                    _get_keycloak_import_error_details(self)
                )
                if error_details:
                    raise CommandError(
                        f"Keycloak import_authorization_settings returned False ({error_details})"
                    )
                raise CommandError("Keycloak import_authorization_settings returned False")

            logger.info("Successfully imported updated authorization settings")

        except Exception as e:
            had_primary_error = e
            logger.error(f"Error processing model changes: {e}")
            logger.error(f"Full traceback: {traceback.format_exc()}")
            raise
        finally:
            # Always try to restore client settings snapshot
            try:
                self._restore_client_settings(client_snapshot)
            except Exception as restore_err:
                logger.error(f"Failed to restore Keycloak client settings snapshot: {restore_err}")
                logger.error(traceback.format_exc())
                if had_primary_error is not None:
                    raise Exception(
                        f"Primary failure occurred and snapshot restore ALSO failed: {restore_err}"
                    ) from had_primary_error
                raise


class Command(BaseCommand):
    help = "Sync Keycloak authorization settings with Django model changes"

    def add_arguments(self, parser):
        parser.add_argument("--no-makemigrations", action="store_true", help="Will skip making migrations")
        parser.add_argument(
            "--skip-migrations",
            action="store_true",
            help="Skip the entire migration step (both makemigrations and migrate)",
        )
        parser.add_argument(
            "--migration-verbosity",
            type=int,
            default=1,
            help="Verbosity level for migrate/makemigrations (0-3, default: 1)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Show what would be changed without making changes")
        parser.add_argument(
            "--preserve-renamed-permissions",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Preserve permissions when renaming models (default: True)",
        )
        parser.add_argument(
            "--check-missing",
            action=argparse.BooleanOptionalAction,
            default=True,
            help="Check for Django models missing from Keycloak and add them (default: True).",
        )
        parser.add_argument(
            "--bootstrap",
            action=argparse.BooleanOptionalAction,
            default=False,
            help="If Keycloak env vars are missing, start bootstrap flow (default: False).",
        )
        parser.add_argument(
            "--ensure-default-authz",
            action="store_true",
            default=False,
            help="Ensure a default resource, regex default policy and resource-based default permission exist",
        )
        parser.add_argument(
            "--makemigrations-args",
            type=str,
            default="",
            help='Extra arguments to forward to makemigrations as a quoted string. Example: "--merge --empty myapp"',
        )
        parser.add_argument(
            "--migrate-args",
            type=str,
            default="",
            help='Extra arguments to forward to migrate as a quoted string. Example: "--run-syncdb" or "--fake myapp 0001"',
        )
        parser.add_argument(
            "--sync-retries",
            type=int,
            default=3,  # fail-fast by default
            help="Number of attempts for the Keycloak sync step (default: 1).",
        )

    def check_unapplied_migrations(self, database: str = DEFAULT_DB_ALIAS) -> bool:
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connections[database])
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        return len(plan) > 0

    @classmethod
    def _database_alias_from_migrate_args(cls, raw: str | None) -> str:
        _, options = cls._parse_extra_args(raw or "")
        database = options.get("database")
        if isinstance(database, str) and database.strip():
            return database
        return DEFAULT_DB_ALIAS

    @staticmethod
    def _parse_extra_args(raw: str):
        if not raw or not raw.strip():
            return [], {}

        tokens = shlex.split(raw)
        positional = []
        options = {}
        i = 0
        while i < len(tokens):
            token = tokens[i]
            if token.startswith("--"):
                stripped = token.lstrip("-")
                if "=" in stripped:
                    key, value = stripped.split("=", 1)
                    options[key.replace("-", "_")] = value
                    i += 1
                else:
                    key = stripped.replace("-", "_")
                    if i + 1 < len(tokens) and not tokens[i + 1].startswith("-"):
                        options[key] = tokens[i + 1]
                        i += 2
                    else:
                        options[key] = True
                        i += 1
            else:
                positional.append(token)
                i += 1
        return positional, options

    def execute_migrations(
        self,
        verbosity=1,
        create_new=True,
        makemigrations_extra=None,
        migrate_extra=None,
    ) -> None:
        """
        Fail-fast: any error raises CommandError.
        """
        try:
            database_alias = self._database_alias_from_migrate_args(migrate_extra)
            if create_new:
                mm_pos, mm_opts = self._parse_extra_args(makemigrations_extra or "")
                self.stdout.write("Creating new migrations for model changes...")
                call_command(
                    "makemigrations",
                    *mm_pos,
                    verbosity=verbosity,
                    interactive=False,
                    stdout=self.stdout,
                    stderr=self.stderr,
                    no_input=True,
                    **mm_opts,
                )
                self.stdout.write("✓ New migrations created successfully")

            if not self.check_unapplied_migrations(database=database_alias):
                self.stdout.write("No unapplied migrations found.")
                return

            mig_pos, mig_opts = self._parse_extra_args(migrate_extra or "")
            self.stdout.write("Applying unapplied migrations...")
            call_command(
                "migrate",
                *mig_pos,
                verbosity=verbosity,
                interactive=False,
                stdout=self.stdout,
                stderr=self.stderr,
                **mig_opts,
            )
            self.stdout.write("✓ Django migrations completed successfully")

        except Exception as e:
            logger.error(f"Migration execution failed: {e}")
            logger.error(traceback.format_exc())
            raise CommandError(f"Migration failed: {e}") from e

    def handle(self, *args, **options):
        """
        Fail-fast top-level: any unhandled exception aborts the command.
        """
        try:
            dry_run = options.get("dry_run", False)
            preserve_permissions = options.get("preserve_renamed_permissions", True)
            check_missing = options.get("check_missing", True)
            bootstrap = options.get("bootstrap", False)
            skip_migrations = options.get("skip_migrations", False)
            migration_verbosity = options.get("migration_verbosity", 1)
            no_makemigrations = options.get("no_makemigrations", False)
            ensure_default_authz = options.get("ensure_default_authz", False)
            sync_retries = max(1, int(options.get("sync_retries", 1)))

            # Bootstrap if env missing
            missing = get_missing_keycloak_env() if bootstrap else []
            if missing:
                state = str(uuid.uuid4())

                server, port = start_callback_server(
                    state=state,
                    env_file=ENV_FILE,
                    state_file=STATE_FILE,
                    host="127.0.0.1",
                    port=0,
                )
                callback_url = f"http://127.0.0.1:{port}/callback"

                url = build_instance_controller_url(state, callback_url)

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

                # opening browser is best-effort (not a fatal "error" if it returns False)
                try:
                    webbrowser.open(url)
                except Exception as e:
                    raise CommandError(f"Failed to open browser for bootstrap URL: {e}") from e

                wait_for_keycloak_setup(state, timeout_seconds=900, poll_interval=3)
                self.stdout.write("Keycloak configured. Continuing init...")

            self.stdout.write("=" * 80)
            self.stdout.write("Django Migration + Keycloak Authorization Sync")
            self.stdout.write("=" * 80)

            # Initialize the sync manager.
            sync_manager = KeycloakSyncManager()

            # Detect model changes BEFORE migrations
            self.stdout.write("Detecting model changes BEFORE migrations...")
            questioner = MigrationQuestioner(defaults={"ask_rename_model": True})
            loader = MigrationLoader(None, ignore_no_migrations=True)
            autodetector = MigrationAutodetector(
                loader.project_state(),
                ProjectState.from_apps(apps),
                questioner=questioner,
            )
            changes = autodetector.changes(graph=loader.graph)

            adds: List[Tuple[str, str]] = []
            deletes: List[Tuple[str, str]] = []
            renames: List[Tuple[str, str, str]] = []

            for app_label, migrations in changes.items():
                if "Historical" in app_label:
                    continue
                for migration in migrations:
                    for operation in migration.operations:
                        if isinstance(operation, CreateModel):
                            adds.append((app_label, operation.name))
                        elif isinstance(operation, DeleteModel):
                            deletes.append((app_label, operation.name))
                        elif isinstance(operation, RenameModel):
                            renames.append((app_label, operation.old_name, operation.new_name))

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

            # Migrations
            if not skip_migrations:
                self.stdout.write("\n" + "-" * 80)
                self.stdout.write("Executing Django Migrations")
                self.stdout.write("-" * 80)

                database_alias = self._database_alias_from_migrate_args(options.get("migrate_args"))
                db_engine = (
                    settings.DATABASES.get(database_alias, {}).get("ENGINE", "")
                    if isinstance(settings.DATABASES, dict)
                    else ""
                )
                if "postgresql" in db_engine:
                    call_command("create_db", database=database_alias)
                call_command("createcachetable", database=database_alias)

                if dry_run:
                    self.stdout.write("DRY RUN: Would execute Django migrations")
                    if self.check_unapplied_migrations(database=database_alias):
                        self.stdout.write("Pending migrations found - would be executed")
                    else:
                        self.stdout.write("No pending migrations found")
                else:
                    makemigrations_args = options.get("makemigrations_args", "")
                    migrate_args = options.get("migrate_args", "")
                    self.execute_migrations(
                        verbosity=migration_verbosity,
                        create_new=not no_makemigrations,
                        makemigrations_extra=makemigrations_args,
                        migrate_extra=migrate_args,
                    )
            else:
                self.stdout.write("\nSkipping Django migrations (--skip-migrations)")

            if dry_run:
                self.stdout.write("Dry run mode - no changes will be made.")
                return

            # Check missing
            # Check missing
            missing_models: Set[str] = set()
            if check_missing:
                # Phase 1: Check missing models with retry
                self.stdout.write("Checking for missing models...")
                for attempt in range(1, sync_retries + 1):
                    try:
                        auth_config = sync_manager.export_configs()
                        all_django_models = sync_manager.get_all_django_models()
                        existing_keycloak_resources = sync_manager.get_existing_keycloak_resources(auth_config)
                        missing_models = sync_manager.find_missing_models(
                            all_django_models, existing_keycloak_resources, set()
                        )

                        # if rename exists, don't double-add the new name as "missing"
                        for app_name, old_name, new_name in renames:
                            missing_models.discard(f"{app_name}.{new_name}")

                        if missing_models:
                            self.stdout.write(f"Would add {len(missing_models)} missing models:")
                            for model_name in sorted(missing_models):
                                self.stdout.write(f"  WOULD ADD: {model_name}")
                        else:
                            self.stdout.write("No missing models found.")
                        
                        break  # Success
                    except Exception as e:
                        if attempt >= sync_retries:
                            raise CommandError(
                                f"Failed to check missing models after {sync_retries} attempt(s): {e}"
                            ) from e
                        
                        wait = 2 ** attempt
                        self.stderr.write(
                            f"Check missing models attempt {attempt}/{sync_retries} failed: {e}\n"
                            f"  Retrying in {wait}s..."
                        )
                        time.sleep(wait)

            # Sync to Keycloak
            self.stdout.write("\nSyncing changes to Keycloak...")
            missing_models_tuples = {tuple(m.split(".")) for m in missing_models}
            missing_models_tuples = missing_models_tuples.union(set(adds))
            new_missing_models_list = list(missing_models_tuples)

            last_err: Exception | None = None
            for attempt in range(1, sync_retries + 1):
                try:
                    sync_manager.process_model_changes(
                        new_missing_models_list,
                        deletes,
                        renames,
                        preserve_permissions=preserve_permissions,
                        ensure_default_authz=ensure_default_authz,
                    )
                    self.stdout.write("✓ All model changes successfully synced to Keycloak!")
                    return
                except Exception as e:
                    last_err = e
                    if attempt >= sync_retries:
                        if _is_non_fatal_keycloak_import_timeout(sync_manager):
                            error_details = _format_keycloak_import_error_details(
                                _get_keycloak_import_error_details(sync_manager)
                            )
                            warning = (
                                "Keycloak authorization import did not complete after "
                                f"{sync_retries} attempt(s)"
                            )
                            if error_details:
                                warning = f"{warning} ({error_details})"
                            warning = f"{warning}; continuing init without aborting."
                            logger.warning(warning)
                            self.stderr.write(warning)
                            return

                        raise CommandError(
                            f"Keycloak sync failed after {sync_retries} attempt(s): {e}"
                        ) from e

                    wait = 2 ** attempt
                    self.stderr.write(
                        f"Keycloak sync attempt {attempt}/{sync_retries} failed: {e}\n"
                        f"  Retrying in {wait}s..."
                    )
                    time.sleep(wait)

            # Should never reach
            raise CommandError(f"Keycloak sync failed: {last_err}")

        except CommandError:
            # already a clean failure
            raise
        except UnicodeDecodeError as e:
            raise CommandError(
                format_db_connection_unicode_error(e, settings.DATABASES.get("default", {}))
            ) from e
        except Exception as e:
            # convert any unexpected error into a failing exit code
            raise CommandError(f"init command failed: {e}") from e
