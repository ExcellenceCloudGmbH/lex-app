from urllib.parse import urlparse

from django.conf import settings
from django.http import JsonResponse
from django.utils.http import url_has_allowed_host_and_scheme

from lex.api.utils.api_key_requests import (
    is_api_key_request,
    is_instance_api_key_request,
)
from oauth2_authcodeflow.middleware import LoginRequiredMiddleware


class ApiKeyAwareLoginRequiredMiddleware(LoginRequiredMiddleware):
    """
    Allow machine-to-machine API key requests to reach DRF permission checks
    without being rejected by the global OIDC login middleware first.
    """

    def check_login_required(self, request):
        if is_api_key_request(request) or is_instance_api_key_request(request):
            return
        return super().check_login_required(request)


class OidcRedirectGuardMiddleware:
    """Keep ``next``/``fail`` of the OIDC sign-in and sign-out views on allowed origins.

    ``oauth2_authcodeflow`` follows these parameters without checking them,
    which is an open redirect. With ``LEX_SEC_OIDC_REDIRECT`` on (the default)
    a target on ``/oidc/*`` (``authenticate``, ``logout``, ``total_logout``, …)
    must be relative, on the request's own host, or on an origin listed in
    ``CSRF_TRUSTED_ORIGINS``, ``CORS_ALLOWED_ORIGINS`` or
    ``CORS_ORIGIN_WHITELIST`` (``LEX_CORS_ALLOWED_ORIGINS`` sets the latter
    two; ``https://*.example.com`` wildcards included). Anything else answers
    400 before the OIDC view runs.
    """

    PARAMS = ("next", "fail")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from lex.core.security_flags import strict

        if strict("OIDC_REDIRECT") and self._unsafe(request):
            detail = "Redirect target not allowed."
            return JsonResponse({"code": "REDIRECT_NOT_ALLOWED", "detail": detail, "error": detail}, status=400)
        return self.get_response(request)

    @staticmethod
    def _configured_origins():
        for name in ("CSRF_TRUSTED_ORIGINS", "CORS_ALLOWED_ORIGINS", "CORS_ORIGIN_WHITELIST"):
            for origin in getattr(settings, name, ()) or ():
                origin = str(origin).strip().rstrip("/")
                if origin and origin != "*":
                    yield origin

    @classmethod
    def _origin_allowed(cls, scheme, netloc):
        origin = f"{scheme}://{netloc}"
        for allowed in cls._configured_origins():
            if allowed.lower() == origin:
                return True
            # CSRF_TRUSTED_ORIGINS style wildcard: https://*.example.com
            if "://*." in allowed:
                allowed_scheme, _, domain = allowed.lower().partition("://*.")
                if scheme == allowed_scheme and (netloc == domain or netloc.endswith("." + domain)):
                    return True
        return False

    @classmethod
    def _target_allowed(cls, target, request):
        # Relative targets and the site's own host (Django's own check, which
        # also refuses "//evil", "/\\evil", control characters and "javascript:").
        try:
            hosts = {request.get_host()}
        except Exception:  # DisallowedHost: only configured origins may pass
            hosts = set()
        if url_has_allowed_host_and_scheme(target, allowed_hosts=hosts):
            return True
        parsed = urlparse(target.strip())
        if parsed.scheme not in ("http", "https") or not parsed.netloc or "@" in parsed.netloc:
            return False
        return cls._origin_allowed(parsed.scheme.lower(), parsed.netloc.lower())

    @classmethod
    def _unsafe(cls, request):
        if "/oidc/" not in request.path:
            return False
        for name in cls.PARAMS:
            for target in request.GET.getlist(name):
                if target and not cls._target_allowed(target, request):
                    return True
        return False
