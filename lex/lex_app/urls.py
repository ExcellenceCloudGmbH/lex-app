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
from lex.api.views.calculations.ActiveCalculations import ActiveCalculations
from lex.authentication.views.handoff_views import HandoffView
from lex.authentication.views.session_views import AuthCompleteView, SessionStatusView
from lex.authentication.views.user_api import CurrentUser
from lex.lex_app import settings
from lex.process_admin.settings import processAdminSite, adminSite
from lex.react.views import serve_react

from . import views

# lex-surfaces-reflex-ws: connects the receiver that lets Keycloak's back-channel logouts reach
# Reflex dashboards (lex:kc_sid_ended:{sid}); loaded wherever /oidc/backchannel-logout is routed.
from lex.lex_app.reflex import backchannel as _reflex_backchannel  # noqa: E402,F401

_raw_base_path = os.getenv("DJANGO_BASE_PATH")
url_prefix = _raw_base_path.strip("/") if _raw_base_path else ""
admin_route = f"{url_prefix}/admin/" if url_prefix else "admin/"
process_admin_route = f"{url_prefix}/" if url_prefix else ""

urlpatterns = [
    path("health", views.HealthCheck.as_view(), name="health_view"),
    path("api/health", views.HealthCheck.as_view(), name="api_health_view"),
    path("api/active-calculations", ActiveCalculations.as_view(), name="active-calculations"),
    path("api/user/", CurrentUser.as_view(), name="current-user"),
    # HTTP auth contract v2: sign-in status + capabilities, and the page a silent/popup
    # sign-in ends on. Both before the oidc/ include and the process-admin catch-alls.
    re_path(r"^api/auth/session/?$", SessionStatusView.as_view(), name="auth-session"),
    re_path(r"^oidc/auth-complete/?$", AuthCompleteView.as_view(), name="oidc-auth-complete"),
    # The Streamlit dashboard hand-off (LEX_STREAMLIT_AUTH dual/handoff), before the oidc/ include.
    re_path(r"^oidc/handoff/?$", HandoffView.as_view(), name="oidc-handoff"),
    path("api/quackback-widget-token", views.quackback_widget_token),
    path("api/quackback-widget-token/", views.quackback_widget_token, name="quackback-widget-token"),
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
