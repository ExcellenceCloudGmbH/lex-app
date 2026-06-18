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
