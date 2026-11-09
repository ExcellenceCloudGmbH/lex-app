import logging
from datetime import datetime, timezone

import jwt
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from oauth2_authcodeflow import responses as oidc_responses
from oauth2_authcodeflow.tokens import get_access_token

logger = logging.getLogger(__name__)


#: How long before the token actually expires the client should renew it. The
#: embedded Streamlit iframe cannot re-authenticate itself (the proxy would have
#: to frame Keycloak, which forbids it), so the caller must renew *before* the
#: token dies rather than react afterwards.
TOKEN_REFRESH_SKEW_SECONDS = 60

#: What ``LEX_STREAMLIT_AUTH=handoff`` hands out instead of a token. Not a JWT, and nothing
#: accepts it: a frontend that predates the hand-off still gets a "token" to put in the
#: dashboard URL (``auth_token``) and to POST to ``/auth/adopt``, both of which the proxy then
#: ignores -- it signs the dashboard in through the hand-off instead.
HANDOFF_PLACEHOLDER_TOKEN = "lex-dashboard-handoff"
#: How long the placeholder claims to last, so an old frontend asks again rarely.
HANDOFF_PLACEHOLDER_SECONDS = 3600


def _handoff_placeholder() -> Response:
    """The ``handoff``-mode answer: the shape old frontends expect, carrying no credential."""
    now = int(datetime.now(timezone.utc).timestamp())
    response = Response(
        {
            "token": HANDOFF_PLACEHOLDER_TOKEN,
            "expires_at": datetime.fromtimestamp(now + HANDOFF_PLACEHOLDER_SECONDS, timezone.utc).isoformat(),
            "expires_in": HANDOFF_PLACEHOLDER_SECONDS,
            "refresh_interval": HANDOFF_PLACEHOLDER_SECONDS - TOKEN_REFRESH_SKEW_SECONDS,
            "dashboard_handoff": True,
        },
        status=status.HTTP_200_OK,
    )
    response["Cache-Control"] = "no-store"
    return response


def _access_token_expiry(request, token: str) -> int | None:
    """Epoch seconds at which ``token`` stops being accepted, or ``None``.

    Prefers the expiry the sign-in records (``request.oidc.access_expires_at``);
    falls back to the token's own ``exp``. The signature is deliberately not
    verified here -- this is our own sign-in's token being read for its expiry,
    and the proxy is the component that authenticates it (against Keycloak's JWKS).
    """
    context = getattr(getattr(request, "_request", request), "oidc", None)
    expires_at = getattr(context, "access_expires_at", None)
    if expires_at is not None:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return int(expires_at.timestamp())

    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        exp = claims.get('exp')
        return int(exp) if exp else None
    except jwt.PyJWTError:
        logger.warning("Could not read exp from the sign-in's access token")
        return None


class StreamlitTokenView(APIView):
    """Hands the embedded Streamlit iframe the caller's Keycloak access token.

    The token is the sign-in's own access token (``oauth2_authcodeflow.tokens.get_access_token``,
    refreshed first when it would expire within ``TOKEN_REFRESH_SKEW_SECONDS``), so the auth
    proxy in front of Streamlit can validate it against Keycloak's JWKS. It is short-lived, and
    the proxy has no way to renew it on the embedded path -- it is given no refresh token, and
    deliberately so: a refresh token would have to travel through the browser and into the
    iframe URL, where it would land in access logs, history and ``Referer`` headers.

    Renewal is therefore the caller's job, which is why the response carries the
    expiry alongside the token: the frontend re-requests before ``expires_at``
    and re-sources the iframe, and the proxy adopts the newer token.

    No OIDC sign-in answers the contract's 401; an ended sign-in 401 ``session_expired`` and an
    unreachable Keycloak 503 ``auth_unavailable`` (``get_access_token`` raises, the exception
    handler answers).
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Return the current access token and when the caller must renew it."""
        from lex.authentication.dashboards.modes import HANDOFF, streamlit_auth_mode

        if streamlit_auth_mode() == HANDOFF:
            return _handoff_placeholder()
        token = get_access_token(getattr(request, "_request", request), min_ttl=TOKEN_REFRESH_SKEW_SECONDS)
        if not token:
            # Signed in to Django, but not through OIDC (e.g. an admin login): there is no
            # token to hand out, and only an OIDC sign-in can fix that.
            logger.warning("Streamlit token requested by a sign-in without an OIDC session")
            response = Response(
                {
                    "error": "not_authenticated",
                    "login_url": oidc_responses.login_path(),
                    "detail": "No active OIDC sign-in; re-authentication required.",
                },
                status=status.HTTP_401_UNAUTHORIZED,
            )
            response["WWW-Authenticate"] = oidc_responses.www_authenticate(getattr(request, "_request", request))
            response["Cache-Control"] = "no-store"
            return response

        payload = {'token': token}

        expires_at = _access_token_expiry(request, token)
        if expires_at:
            now = int(datetime.now(timezone.utc).timestamp())
            expires_in = max(0, expires_at - now)
            payload.update({
                'expires_at': datetime.fromtimestamp(expires_at, timezone.utc).isoformat(),
                'expires_in': expires_in,
                # Never negative and never later than expiry, so a token that is
                # already inside the skew window tells the caller to renew now
                # rather than scheduling a refresh in the past.
                'refresh_interval': max(0, expires_in - TOKEN_REFRESH_SKEW_SECONDS),
            })

        response = Response(payload, status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response
