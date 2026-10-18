"""Keycloak connection for the instance's own client, from the environment.

The same settings KeycloakManager.initialize() uses at runtime: the realm comes
from KEYCLOAK_REALM (or KEYCLOAK_REALM_NAME) — whatever realm the IC created the
instance's client in — and the connection authenticates as that client with
client credentials.
"""
import os

from django.conf import settings
from django.core.management.base import CommandError
from keycloak import KeycloakOpenIDConnection


def instance_keycloak_connection():
    """Return (connection, client_uuid) for the instance's Keycloak client."""
    realm = os.getenv("KEYCLOAK_REALM") or os.getenv("KEYCLOAK_REALM_NAME")
    values = {
        "KEYCLOAK_URL": os.getenv("KEYCLOAK_URL"),
        "KEYCLOAK_REALM": realm,
        "OIDC_RP_CLIENT_ID": os.getenv("OIDC_RP_CLIENT_ID"),
        "OIDC_RP_CLIENT_SECRET": os.getenv("OIDC_RP_CLIENT_SECRET"),
        "OIDC_RP_CLIENT_UUID": os.getenv("OIDC_RP_CLIENT_UUID"),
    }
    missing = [name for name, value in values.items() if not value]
    if missing:
        raise CommandError(f"Missing Keycloak settings: {', '.join(missing)}")

    conn = KeycloakOpenIDConnection(
        server_url=values["KEYCLOAK_URL"],
        realm_name=realm,
        client_id=values["OIDC_RP_CLIENT_ID"],
        client_secret_key=values["OIDC_RP_CLIENT_SECRET"],
        verify=getattr(settings, "KEYCLOAK_VERIFY_SSL", False),
    )
    return conn, values["OIDC_RP_CLIENT_UUID"]
