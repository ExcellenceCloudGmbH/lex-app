from rest_framework.authentication import BaseAuthentication

class BearerMiddlewareAuthentication(BaseAuthentication):
    """
    Uses request.user set by oauth2_authcodeflow BearerAuthMiddleware.
    Only activates when an Authorization header is present,
    so SessionAuthentication can still handle browser sessions + CSRF.
    """
    def authenticate(self, request):
        auth = request.META.get("HTTP_AUTHORIZATION", "")
        if not auth.startswith("Bearer "):
            return None

        user = getattr(request._request, "user", None)
        if user and getattr(user, "is_authenticated", False):
            return (user, None)

        return None