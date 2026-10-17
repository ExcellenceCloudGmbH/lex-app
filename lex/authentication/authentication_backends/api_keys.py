from rest_framework.authentication import BaseAuthentication


class LexAPIKeyAuthentication(BaseAuthentication):
    """DRF authentication for scoped API keys (:class:`~lex.authentication.models.LexAPIKey`).

    Returns ``(key.user, key)``. It runs before session authentication so a
    key request is never subject to the session's CSRF check. Without a scoped
    key in the request it returns ``None``.
    """

    def authenticate(self, request):
        from lex.api.utils.api_key_requests import get_scoped_api_key

        key = get_scoped_api_key(request)
        if key is None:
            return None
        if not key.allows_path(request.path):
            return None
        return (key.user, key)

    def authenticate_header(self, request):
        return None
