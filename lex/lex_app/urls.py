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

from django.urls import path, include, re_path
from lex.react.views import serve_react

from lex.lex_app import settings


from . import views
from lex.process_admin.settings import processAdminSite, adminSite
from lex.authentication.views.user_api import CurrentUser

url_prefix = (
    os.getenv("DJANGO_BASE_PATH") if os.getenv("DJANGO_BASE_PATH") is not None else ""
)

urlpatterns = [
    path("health", views.HealthCheck.as_view(), name="health_view"),
    path("api/user/", CurrentUser.as_view(), name="current-user"),
    path(url_prefix + "admin/", adminSite.urls),
    path(url_prefix, processAdminSite.urls),
    # path("oidc/", include("mozilla_django_oidc.urls")),
    path("oidc/", include("oauth2_authcodeflow.urls")),
    re_path(
        r"^(?P<path>.*)$", serve_react, {"document_root": settings.REACT_APP_BUILD_PATH}
    ),
]
