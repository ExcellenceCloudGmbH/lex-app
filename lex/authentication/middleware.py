from oauth2_authcodeflow.middleware import LoginRequiredMiddleware

from lex.api.utils.api_key_requests import is_api_key_request


class ApiKeyAwareLoginRequiredMiddleware(LoginRequiredMiddleware):
    """
    Allow machine-to-machine API key requests to reach DRF permission checks
    without being rejected by the global OIDC login middleware first.

    Also ensures that AJAX/fetch API requests receive a 401 JSON response
    instead of a 302 redirect to Keycloak when the session has expired.
    The upstream LoginRequiredMiddleware forces a redirect for all GET
    requests, which causes CORS failures when the browser's fetch() follows
    the redirect to the external identity provider.
    """

    def is_api_request(self, request):
        # Detect XHR / fetch requests that expect JSON.
        # The React frontend always sends 'Accept: application/json' on API
        # calls (via raHttpClient, reduxQueryHttpClient, and customFetch).
        # These must never receive a 302 redirect — they need a 401 so the
        # frontend's SessionAuthGate can handle re-authentication properly.
        accept = request.META.get("HTTP_ACCEPT", "")
        if "application/json" in accept:
            return True
        if request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest":
            return True
        return super().is_api_request(request)

    def check_login_required(self, request):
        if is_api_key_request(request):
            return
        return super().check_login_required(request)
