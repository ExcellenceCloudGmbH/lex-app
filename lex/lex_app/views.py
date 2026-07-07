import base64
import hashlib
import hmac
import json
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.views import View
from django.views.decorators.http import require_POST

from lex.lex_app.runtime_health import build_health_payload


class HealthCheck(View):
    authentication_classes = []

    def get(self, request):
        return JsonResponse(build_health_payload())


def _base64_url_encode(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("utf-8")


def _sign_quackback_widget_token(payload):
    secret = getattr(settings, "QUACKBACK_WIDGET_SECRET", "")
    if not secret:
        raise RuntimeError("QUACKBACK_WIDGET_SECRET is not configured")

    header = _base64_url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode("utf-8"))
    body = _base64_url_encode(json.dumps(payload).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"),
        f"{header}.{body}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return f"{header}.{body}.{_base64_url_encode(signature)}"


@login_required
@require_POST
def quackback_widget_token(request):
    user = request.user
    email = getattr(user, "email", "") or ""
    if not email:
        return JsonResponse({"error": "Authenticated user does not have an email"}, status=400)

    get_full_name = getattr(user, "get_full_name", None)
    full_name = get_full_name() if callable(get_full_name) else ""
    name = full_name or getattr(user, "username", "") or email
    now = int(time.time())

    return JsonResponse(
        {
            "ssoToken": _sign_quackback_widget_token(
                {
                    "sub": str(user.pk),
                    "email": email,
                    "name": name,
                    "exp": now + 300,
                }
            )
        }
    )
