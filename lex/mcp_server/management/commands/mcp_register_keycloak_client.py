"""Provision (or inspect) the Keycloak client used by MCP OAuth.

Idempotent: re-running the command prints the existing client config
without modifying anything. Uses the already-wired ``KeycloakManager``
admin connection so no new env vars are needed.

Usage::

    python manage.py mcp_register_keycloak_client \\
        --client-id lex-mcp \\
        --redirect-uri http://localhost:5174/oauth-callback \\
        --redirect-uri http://localhost:33418/oauth/callback
"""
from __future__ import annotations

import json
import os

from django.core.management.base import BaseCommand, CommandError


_DEFAULT_REDIRECT_URIS = ("http://localhost:33418/oauth/callback",)


class Command(BaseCommand):
    help = "Create (or inspect) the Keycloak OAuth client for MCP access."

    def add_arguments(self, parser):
        parser.add_argument(
            "--client-id",
            default="lex-mcp",
            help="Keycloak client_id to create or inspect (default: lex-mcp).",
        )
        parser.add_argument(
            "--name",
            default=None,
            help="Display name for the client (defaults to the client_id).",
        )
        parser.add_argument(
            "--public",
            action="store_true",
            help="Create a public client (PKCE only). Default is confidential.",
        )
        parser.add_argument(
            "--redirect-uri",
            action="append",
            default=None,
            metavar="URI",
            help="Allowed redirect URI; repeat for multiple (default: localhost callback).",
        )
        parser.add_argument(
            "--web-origin",
            action="append",
            default=None,
            metavar="ORIGIN",
            help="Allowed Web Origin for CORS; repeat for multiple.",
        )

    def handle(self, *args, **opts):
        try:
            from lex.api.views.authentication.KeycloakManager import KeycloakManager
        except Exception as exc:
            raise CommandError(f"KeycloakManager unavailable: {exc}") from exc

        kc = KeycloakManager()
        if not getattr(kc, "admin", None):
            raise CommandError(
                "Keycloak admin connection is unconfigured; check OIDC_RP_CLIENT_ID / "
                "OIDC_RP_CLIENT_SECRET / KEYCLOAK_URL."
            )

        client_id = opts["client_id"]
        display_name = opts["name"] or client_id
        is_public = bool(opts["public"])
        redirect_uris = list(opts.get("redirect_uri") or _DEFAULT_REDIRECT_URIS)
        web_origins = list(opts.get("web_origin") or [])

        admin = kc.admin
        existing_uuid = admin.get_client_id(client_id)

        if existing_uuid:
            self.stdout.write(self.style.SUCCESS(
                f"Client '{client_id}' already exists; printing config."
            ))
        else:
            payload = {
                "clientId": client_id,
                "name": display_name,
                "enabled": True,
                "protocol": "openid-connect",
                "publicClient": is_public,
                "standardFlowEnabled": True,
                "directAccessGrantsEnabled": False,
                "serviceAccountsEnabled": False,
                "redirectUris": redirect_uris,
                "webOrigins": web_origins,
                "attributes": {
                    "pkce.code.challenge.method": "S256",
                },
            }
            try:
                admin.create_client(payload, skip_exists=True)
            except Exception as exc:
                raise CommandError(f"create_client failed: {exc}") from exc
            existing_uuid = admin.get_client_id(client_id)
            self.stdout.write(self.style.SUCCESS(
                f"Created Keycloak client '{client_id}' (uuid={existing_uuid})."
            ))

        client_record = admin.get_client(existing_uuid)
        client_secret = None
        if not client_record.get("publicClient", False):
            try:
                secret_blob = admin.get_client_secrets(existing_uuid) or {}
                client_secret = secret_blob.get("value")
            except Exception:
                client_secret = None

        issuer = (
            f"{(os.getenv('KEYCLOAK_URL') or '').rstrip('/')}"
            f"/realms/{kc.realm_name}"
        )
        config = {
            "client_id": client_record.get("clientId"),
            "client_uuid": existing_uuid,
            "public_client": bool(client_record.get("publicClient")),
            "redirect_uris": client_record.get("redirectUris") or [],
            "web_origins": client_record.get("webOrigins") or [],
            "issuer": issuer,
            "authorization_endpoint": f"{issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{issuer}/protocol/openid-connect/token",
            "userinfo_endpoint": f"{issuer}/protocol/openid-connect/userinfo",
            "jwks_uri": f"{issuer}/protocol/openid-connect/certs",
        }
        if client_secret is not None:
            config["client_secret"] = client_secret

        self.stdout.write("\n--- MCP Keycloak client config ---")
        self.stdout.write(json.dumps(config, indent=2))
        self.stdout.write("---")
