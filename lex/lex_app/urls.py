"""lex_app URL Configuration

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/3.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))

A project adds its own endpoints with ``lex_config.API_URLCONF`` (mounted
under ``lex_config.API_PREFIX``, ahead of lex's routes and the frontend
catch-all) — never by replacing ``ROOT_URLCONF``. See
:func:`build_project_urlpatterns`.
"""
import os
import re

from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.core.exceptions import ImproperlyConfigured
from django.urls import path, include, re_path
from lex.api.views.calculations.ActiveCalculations import ActiveCalculations
from lex.authentication.views.user_api import CurrentUser
from lex.lex_app import settings
from lex.process_admin.settings import processAdminSite, adminSite
from lex.react.views import serve_react

from . import views

_raw_base_path = os.getenv("DJANGO_BASE_PATH")
url_prefix = _raw_base_path.strip("/") if _raw_base_path else ""
admin_route = f"{url_prefix}/admin/" if url_prefix else "admin/"
process_admin_route = f"{url_prefix}/" if url_prefix else ""

#: Path prefixes owned by lex. A project API prefix must not overlap any of them.
RESERVED_PREFIXES = (
    "health",
    "admin/",
    "oidc/",
    "static-django/",
    "api/health",
    "api/user",
    "api/active-calculations",
    "api/quackback-widget-token",
    "api/model_entries/",
    "api/model-structure",
    "api/model_info/",
    "api/run_step/",
    "api/htmlreport/",
    "api/global-search/",
    "api/calculationlog/",
    "api/project-info",
    "api/widget_structure",
    "api/init-calculation-logs",
    "api/clean-calculations",
    "api/logs",
    "api/download-pdf/",
    "api/user_permissions/",
    "api/auth/",
    "api/delete-unused-files",
)


def _check_project_prefix(prefix: str) -> None:
    if prefix in ("api/",):
        raise ImproperlyConfigured(
            "lex_config.API_PREFIX must be more specific than 'api/' (e.g. 'api/myproject/')"
        )
    for reserved in RESERVED_PREFIXES:
        reserved_dir = reserved if reserved.endswith("/") else reserved + "/"
        if prefix.startswith(reserved_dir) or reserved_dir.startswith(prefix):
            raise ImproperlyConfigured(
                f"lex_config.API_PREFIX {prefix!r} overlaps lex's own route {reserved!r}"
            )


def build_project_urlpatterns():
    """URL patterns of the project API (``lex_config.API_URLCONF`` and ``SCHEMA``).

    Empty unless configured. Called at import time below and by tests that
    change the configuration.
    """
    from lex.api.schema import schema_urlpatterns
    from lex.core import extensions

    urlconf = extensions.get_setting("API_URLCONF")
    has_schema = bool(extensions.get_setting("SCHEMA"))
    if not urlconf and not has_schema:
        return []
    prefix = extensions.api_prefix()
    _check_project_prefix(prefix)
    route = f"{url_prefix}/{prefix}" if url_prefix else prefix
    patterns = []
    if urlconf:
        patterns.append(path(route, include(urlconf)))
    if has_schema:
        patterns.append(path(route, include(schema_urlpatterns())))
    return patterns


def build_urlpatterns():
    patterns = [
        path("health", views.HealthCheck.as_view(), name="health_view"),
        path("api/health", views.HealthCheck.as_view(), name="api_health_view"),
        path("api/active-calculations", ActiveCalculations.as_view(), name="active-calculations"),
        path("api/user/", CurrentUser.as_view(), name="current-user"),
        path("api/quackback-widget-token", views.quackback_widget_token),
        path("api/quackback-widget-token/", views.quackback_widget_token, name="quackback-widget-token"),
        *build_project_urlpatterns(),
        path(admin_route, adminSite.urls),
        path(process_admin_route, processAdminSite.urls),
        # path("oidc/", include("mozilla_django_oidc.urls")),
        path("oidc/", include("oauth2_authcodeflow.urls")),
        re_path(
            r"^static-django/(?P<path>.*)$",
            staticfiles_serve,
            {"insecure": True},
        ),
    ]

    if url_prefix:
        patterns.append(
            re_path(
                rf"^{re.escape(url_prefix)}/static-django/(?P<path>.*)$",
                staticfiles_serve,
                {"insecure": True},
            )
        )

    patterns += [
        re_path(
            r"^(?P<path>.*)$", serve_react, {"document_root": settings.REACT_APP_BUILD_PATH}
        ),
    ]
    return patterns


urlpatterns = build_urlpatterns()
