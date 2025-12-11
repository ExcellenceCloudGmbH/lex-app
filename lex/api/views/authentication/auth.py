from urllib.parse import urlencode
from django.conf import settings


def provider_logout(request):
    """
    Build a Keycloak end-session URL that:
    1) passes the last ID token as a hint, and
    2) tells Keycloak where to send the user next.
    """
    base = settings.OIDC_OP_LOGOUT_ENDPOINT
    id_token = request.session.get("oidc_id_token")

    if not id_token:
        # fall back to simply clearing Django session
        return base

    # Keycloak (19+) expects:
    #  - id_token_hint
    #  - post_logout_redirect_uri
    params = {
        "id_token_hint": id_token,
        "post_logout_redirect_uri": settings.LOGOUT_REDIRECT_URL,
    }

    return f"{base}?{urlencode(params)}"
