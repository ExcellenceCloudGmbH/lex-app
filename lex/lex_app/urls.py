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
"""
import os
import re

from django.contrib.staticfiles.views import serve as staticfiles_serve
from django.urls import path, include, re_path
from lex.authentication.views.user_api import CurrentUser
from lex.lex_app import settings
from lex.process_admin.settings import processAdminSite, adminSite
from lex.react.views import serve_react

from . import views

_raw_base_path = os.getenv("DJANGO_BASE_PATH")
url_prefix = _raw_base_path.strip("/") if _raw_base_path else ""
admin_route = f"{url_prefix}/admin/" if url_prefix else "admin/"
process_admin_route = f"{url_prefix}/" if url_prefix else ""

urlpatterns = [
    path("health", views.HealthCheck.as_view(), name="health_view"),
    path("api/health", views.HealthCheck.as_view(), name="api_health_view"),
    path("api/user/", CurrentUser.as_view(), name="current-user"),
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
    urlpatterns.append(
        re_path(
            rf"^{re.escape(url_prefix)}/static-django/(?P<path>.*)$",
            staticfiles_serve,
            {"insecure": True},
        )
    )

urlpatterns += [
    re_path(
        r"^(?P<path>.*)$", serve_react, {"document_root": settings.REACT_APP_BUILD_PATH}
    ),
]
