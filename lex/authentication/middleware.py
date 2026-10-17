from urllib.parse import urlparse

from django.conf import settings
from django.http import JsonResponse

from lex.api.utils.api_key_requests import (
    is_api_key_request,
    is_instance_api_key_request,
)
from oauth2_authcodeflow.middleware import LoginRequiredMiddleware


class ApiKeyAwareLoginRequiredMiddleware(LoginRequiredMiddleware):
    """
    Allow machine-to-machine API key requests to reach DRF permission checks
    without being rejected by the global OIDC login middleware first.

    Public project paths (``lex_config.PUBLIC_PATHS`` and views with
    ``public = True`` under ``lex_config.API_PREFIX``) need no sign-in either.
    """

    def __call__(self, request):
        from lex.api.utils.api_key_requests import get_scoped_api_key

        scoped_key = get_scoped_api_key(request)
        if scoped_key is not None:
            # The key works only below its allowed path prefixes; elsewhere the
            # path does not exist for it.
            if not scoped_key.allows_path(request.path):
                return JsonResponse({"code": "NOT_FOUND", "detail": "Not found.", "error": "Not found."}, status=404)
            # The request runs as the key's user: permissions, audit actor and
            # database context see that user.
            request.user = scoped_key.user
        return super().__call__(request)

    def is_login_required_for_url(self, request):
        from lex.api.middleware.generic_api_gate import generic_api_hidden_from_anonymous
        from lex.api.project.public_paths import is_public_path

        if is_public_path(request.path):
            return False
        # A closed generic API answers 404 to everyone (GENERIC_API), anonymous
        # callers included: let them through to the gate instead of to sign-in.
        if generic_api_hidden_from_anonymous(request):
            return False
        return super().is_login_required_for_url(request)

    def check_login_required(self, request):
        if is_api_key_request(request) or is_instance_api_key_request(request):
            return
        return super().check_login_required(request)


class OidcRedirectGuardMiddleware:
    """Keep ``next``/``fail`` of the OIDC sign-in and sign-out views on allowed origins.

    ``oauth2_authcodeflow`` follows these parameters without checking them, which
    is an open redirect. With ``LEX_SEC_OIDC_REDIRECT`` on (the default) a target
    must be relative, the request's own origin, or an origin listed in
    ``CORS_ALLOWED_ORIGINS``, ``CORS_ORIGIN_WHITELIST`` or ``CSRF_TRUSTED_ORIGINS``
    (``https://*.example.com`` wildcards included); anything else answers 400.
    """

    PARAMS = ("next", "fail")

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from lex.core.security_flags import strict

        if strict("OIDC_REDIRECT") and self._unsafe(request):
            return JsonResponse(
                {
                    "code": "REDIRECT_NOT_ALLOWED",
                    "detail": "Redirect target not allowed.",
                    "error": "Redirect target not allowed.",
                },
                status=400,
            )
        return self.get_response(request)

    @staticmethod
    def _configured_origins():
        for name in ("CORS_ALLOWED_ORIGINS", "CORS_ORIGIN_WHITELIST", "CSRF_TRUSTED_ORIGINS"):
            for origin in getattr(settings, name, ()) or ():
                if origin and origin != "*":
                    yield origin.rstrip("/")

    @classmethod
    def _origin_allowed(cls, origin, request):
        if origin == f"{request.scheme}://{request.get_host()}":
            return True
        for allowed in cls._configured_origins():
            if allowed == origin:
                return True
            # CSRF_TRUSTED_ORIGINS style wildcard: https://*.example.com
            if "://*." in allowed:
                scheme, _, domain = allowed.partition("://*.")
                parsed = urlparse(origin)
                if parsed.scheme == scheme and (
                    parsed.netloc == domain or parsed.netloc.endswith("." + domain)
                ):
                    return True
        return False

    @classmethod
    def _unsafe(cls, request):
        if "/oidc/" not in request.path:
            return False
        for name in cls.PARAMS:
            target = request.GET.get(name)
            if not target:
                continue
            if target.startswith("//") or target.startswith("\\"):
                return True
            parsed = urlparse(target)
            if not parsed.scheme and not parsed.netloc:
                continue
            if not cls._origin_allowed(f"{parsed.scheme}://{parsed.netloc}", request):
                return True
        return False


class StatelessBearerSessionMiddleware:
    """Keep bearer requests from creating a session (``LEX_BEARER_STATELESS=true``).

    ``oauth2_authcodeflow``'s ``BearerAuthMiddleware`` saves a new session for
    every bearer request without a session cookie — one ``django_session`` row
    per API call from a token client. Placed right before it, this middleware
    makes that request's session non-persisting, so the user is authenticated
    from the token and nothing is written. Default (unset): no effect.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import os

        stateless = os.getenv("LEX_BEARER_STATELESS", "").strip().lower() in ("1", "true", "yes", "on")
        session = getattr(request, "session", None)
        if (
            stateless
            and session is not None
            and request.headers.get("Authorization", "").startswith("Bearer ")
            and not session.session_key
        ):
            session.save = _do_not_persist
        return self.get_response(request)


def _do_not_persist(*args, **kwargs):
    return None
