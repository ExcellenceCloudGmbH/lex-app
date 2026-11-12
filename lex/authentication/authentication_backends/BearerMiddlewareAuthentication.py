from rest_framework.authentication import BaseAuthentication

from oauth2_authcodeflow import responses as oidc_responses


class BearerMiddlewareAuthentication(BaseAuthentication):
    """The user that ``oauth2_authcodeflow``'s ``BearerAuthMiddleware`` authenticated from an
    ``Authorization: Bearer <access token>`` header.

    Only a request whose *bearer token itself* was validated counts (``request.oidc.is_bearer``).
    A browser request that merely carries some ``Authorization: Bearer ...`` header next to its
    session cookie is left to ``OIDCSessionAuthentication``, which enforces CSRF -- otherwise any
    header value would switch CSRF protection off for the session user.

    ``authenticate_header`` makes DRF answer "not authenticated" with **401** and a
    ``WWW-Authenticate`` header (DRF falls back to 403 when the first class has none): a
    ``Bearer error="invalid_token"`` challenge for a rejected bearer token, otherwise
    ``Session realm="<client id>"`` -- the HTTP auth contract v2.
    """

    def authenticate(self, request):
        django_request = getattr(request, "_request", request)
        context = getattr(django_request, "oidc", None)
        if context is None or not getattr(context, "is_bearer", False):
            return None
        user = getattr(django_request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            return (user, None)
        return None

    def authenticate_header(self, request):
        return oidc_responses.www_authenticate(getattr(request, "_request", request))
