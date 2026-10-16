"""OpenAPI schema of a project's own API (drf-spectacular).

Configure it in ``lex_config.py``::

    SCHEMA = {
        "title": "Atrium API",
        "version": "1.4.0",
        "include": ["api/atrium/"],   # default: [API_PREFIX]
        "public": True,               # serve schema/docs without sign-in
        "settings": {},               # further SPECTACULAR_SETTINGS overrides
    }

With ``SCHEMA`` set, ``<API_PREFIX>schema`` serves the OpenAPI document and
``<API_PREFIX>docs`` a Redoc page; ``lex schema --file openapi.yaml`` writes
it for client generation. Only paths under ``include`` are documented; lex's
generic ``model_entries`` endpoints are left out.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from lex.core import extensions


def _base_path() -> str:
    raw = os.getenv("DJANGO_BASE_PATH", "").strip().strip("/")
    return f"/{raw}" if raw else ""


def schema_config() -> Dict[str, Any]:
    config = extensions.get_setting("SCHEMA") or {}
    if not isinstance(config, dict):
        from django.core.exceptions import ImproperlyConfigured

        raise ImproperlyConfigured("lex_config.SCHEMA must be a dict")
    return config


def included_prefixes() -> List[str]:
    config = schema_config()
    include = config.get("include")
    if include is None:
        include = [extensions.api_prefix()] if extensions.get_setting("API_URLCONF") else []
    base = _base_path()
    return [f"{base}/{str(prefix).strip('/')}/" for prefix in include]


def include_configured_paths(endpoints):
    """drf-spectacular preprocessing hook: keep only the configured prefixes."""
    prefixes = included_prefixes()
    if not prefixes:
        return []
    return [endpoint for endpoint in endpoints if endpoint[0].startswith(tuple(prefixes))]


def custom_settings() -> Dict[str, Any]:
    """SPECTACULAR_SETTINGS overrides derived from ``lex_config.SCHEMA``."""
    config = schema_config()
    overrides: Dict[str, Any] = dict(config.get("settings") or {})
    if config.get("title"):
        overrides["TITLE"] = config["title"]
    if config.get("version"):
        overrides["VERSION"] = str(config["version"])
    if config.get("description"):
        overrides["DESCRIPTION"] = config["description"]
    return overrides


def schema_urlpatterns():
    """URL patterns for ``schema`` and ``docs``, relative to the API prefix."""
    from django.urls import path
    from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

    if not extensions.get_setting("SCHEMA"):
        return []
    public = bool(schema_config().get("public"))
    extra: Dict[str, Any] = {"custom_settings": custom_settings()}
    if public:
        extra["authentication_classes"] = []
        extra["permission_classes"] = []
    schema_view = SpectacularAPIView.as_view(**extra)
    docs_view = SpectacularRedocView.as_view(
        url_name="lex-project-schema",
        **({"authentication_classes": [], "permission_classes": []} if public else {}),
    )
    schema_view.lex_public = public
    docs_view.lex_public = public
    return [
        path("schema", schema_view, name="lex-project-schema"),
        path("docs", docs_view, name="lex-project-docs"),
    ]


def generate_schema(fmt: str = "openapi") -> Any:
    """Build the schema document (dict) with the configured overrides."""
    from drf_spectacular.generators import SchemaGenerator
    from drf_spectacular.settings import patched_settings

    with patched_settings(custom_settings()):
        generator = SchemaGenerator()
        return generator.get_schema(request=None, public=True)
