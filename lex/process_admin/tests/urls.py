from django.http import HttpResponse
from django.urls import path


def _ok(_request):
    return HttpResponse("ok")


urlpatterns = [
    path("oidc/authentication", _ok, name="oidc_authentication"),
    path("oidc/callback", _ok, name="oidc_callback"),
    path("oidc/logout", _ok, name="oidc_logout"),
    path("oidc/total-logout", _ok, name="oidc_total_logout"),
    path("oidc/logout-by-op", _ok, name="oidc_logout_by_op"),
]
