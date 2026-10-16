"""Scoped API keys: a key acts as a user (lex 3.0).

Kept in an underscore module so lex's model discovery does not register it
with the generic REST API — key hashes must never be listed there.
"""
from django.conf import settings
from django.db import models
from rest_framework_api_key.models import AbstractAPIKey


class LexAPIKey(AbstractAPIKey):
    """An API key that authenticates as ``user``.

    Requests made with it run as that user: permission methods, the audit
    actor and the database context (``lex_config.DB_CONTEXT``) see the user,
    not an all-powerful technical identity. ``scopes`` are the Keycloak-style
    scopes the default permission methods check (``read``, ``edit``, …);
    ``allowed_path_prefixes`` limits where the key works (empty: anywhere).
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="lex_api_keys",
    )
    scopes = models.JSONField(default=list, blank=True)
    allowed_path_prefixes = models.JSONField(default=list, blank=True)

    class Meta(AbstractAPIKey.Meta):
        abstract = False
        verbose_name = "Lex API key"
        verbose_name_plural = "Lex API keys"

    def allows_path(self, path: str) -> bool:
        prefixes = [str(p).strip() for p in (self.allowed_path_prefixes or []) if str(p).strip()]
        if not prefixes:
            return True
        normalized = "/" + path.lstrip("/")
        return any(normalized.startswith("/" + prefix.lstrip("/")) for prefix in prefixes)
