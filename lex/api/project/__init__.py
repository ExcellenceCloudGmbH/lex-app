"""Public API for a project's own REST endpoints.

Mount a module with ``urlpatterns`` through ``lex_config.API_URLCONF`` (under
``lex_config.API_PREFIX``) and build its views on these classes::

    # lex_config.py
    API_URLCONF = "_api.urls"
    API_PREFIX = "api/myproject/"
    PUBLIC_PATHS = ["me"]

See the "Custom API endpoints" page of the lex-app docs.
"""
from lex.api.errors import LexActionError
from lex.api.project.views import (
    LexAPIView,
    LexWebhookView,
    serialize_for,
    strict_serializer,
    verify_hmac_sha256,
    visible_queryset,
)

__all__ = [
    "LexAPIView",
    "LexWebhookView",
    "LexActionError",
    "serialize_for",
    "strict_serializer",
    "verify_hmac_sha256",
    "visible_queryset",
]
