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

import contextlib
import os
from typing import Any, Dict, Iterator, List

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


def lib_doc_excludes():
    """Classes whose docstrings drf-spectacular never uses as an operation description.

    drf-spectacular takes the first docstring along a view's base classes, stopping at its own and DRF's. lex's base
    views sit before that stop, so a project view without a docstring would be described as "A project endpoint …".
    """
    from drf_spectacular.plumbing import get_lib_doc_excludes

    from lex.api.project.views import LexAPIView, LexWebhookView

    return [*get_lib_doc_excludes(), LexAPIView, LexWebhookView]


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


def _clear_enum_name_overrides() -> None:
    # drf-spectacular caches ENUM_NAME_OVERRIDES on first use and patching the
    # settings does not clear it: without this, enum names would depend on
    # which schema generation ran first in the process.
    try:
        from drf_spectacular.plumbing import _load_enum_name_overrides
    except ImportError:  # pragma: no cover - other drf-spectacular versions
        return
    _load_enum_name_overrides.cache_clear()


@contextlib.contextmanager
def project_schema_settings() -> Iterator[None]:
    """drf-spectacular settings as ``lex_config.SCHEMA`` configures them.

    Used by the served schema, the docs page and ``lex schema`` alike, so all
    three describe the same API.
    """
    from drf_spectacular.settings import patched_settings

    _clear_enum_name_overrides()
    try:
        with patched_settings(custom_settings()):
            yield
    finally:
        _clear_enum_name_overrides()


def _views():
    from drf_spectacular.settings import spectacular_settings
    from drf_spectacular.utils import extend_schema
    from drf_spectacular.views import SpectacularAPIView, SpectacularRedocView

    # extend_schema(exclude=True) as on drf-spectacular's own get(): the schema
    # and docs routes are not part of the API they describe.
    class ProjectSchemaView(SpectacularAPIView):
        @extend_schema(exclude=True)
        def get(self, request, *args, **kwargs):
            with project_schema_settings():
                return super().get(request, *args, **kwargs)

    class ProjectRedocView(SpectacularRedocView):
        # The page's data (title, Redoc bundle) is computed inside get(), so the
        # patched REDOC_DIST and TITLE apply; the template renders it later.
        @extend_schema(exclude=True)
        def get(self, request, *args, **kwargs):
            with project_schema_settings():
                self.title = spectacular_settings.TITLE
                return super().get(request, *args, **kwargs)

    return ProjectSchemaView, ProjectRedocView


def schema_urlpatterns():
    """URL patterns for ``schema`` and ``docs``, relative to the API prefix."""
    from django.urls import path

    if not extensions.get_setting("SCHEMA"):
        return []
    ProjectSchemaView, ProjectRedocView = _views()
    public = bool(schema_config().get("public"))
    extra: Dict[str, Any] = {}
    if public:
        extra["authentication_classes"] = []
        extra["permission_classes"] = []
    schema_view = ProjectSchemaView.as_view(**extra)
    docs_view = ProjectRedocView.as_view(
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

    with project_schema_settings():
        generator = SchemaGenerator()
        return generator.get_schema(request=None, public=True)
