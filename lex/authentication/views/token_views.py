import logging
import uuid
from datetime import datetime, timezone, timedelta

import jwt
from django.conf import settings
from django.core.cache import cache
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

logger = logging.getLogger(__name__)


#: How long before the token actually expires the client should renew it. The
#: embedded Streamlit iframe cannot re-authenticate itself (the proxy would have
#: to frame Keycloak, which forbids it), so the caller must renew *before* the
#: token dies rather than react afterwards.
TOKEN_REFRESH_SKEW_SECONDS = 60


def _access_token_expiry(session, token: str) -> int | None:
    """Epoch seconds at which ``token`` stops being accepted, or ``None``.

    Prefers the value oauth2_authcodeflow already stored on the session; falls
    back to the token's own ``exp``. The signature is deliberately not verified
    here — this is our own session's token being read for its expiry, and the
    proxy is the component that authenticates it (against Keycloak's JWKS).
    """
    stored = session.get('oidc_access_expires_at')
    if stored:
        try:
            return int(stored)
        except (TypeError, ValueError):
            logger.warning("Session oidc_access_expires_at is not an int; falling back to token exp")

    try:
        claims = jwt.decode(token, options={"verify_signature": False, "verify_exp": False})
        exp = claims.get('exp')
        return int(exp) if exp else None
    except jwt.PyJWTError:
        logger.warning("Could not read exp from the session access token")
        return None


class StreamlitTokenView(APIView):
    """Hands the embedded Streamlit iframe the caller's Keycloak access token.

    The token is the session's own access token, so the auth proxy in front of
    Streamlit can validate it against Keycloak's JWKS. It is short-lived, and the
    proxy has no way to renew it on the embedded path — it is given no refresh
    token, and deliberately so: a refresh token would have to travel through the
    browser and into the iframe URL, where it would land in access logs, history
    and ``Referer`` headers.

    Renewal is therefore the caller's job, which is why the response carries the
    expiry alongside the token: the frontend re-requests before ``expires_at``
    and re-sources the iframe, and the proxy adopts the newer token.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Return the current access token and when the caller must renew it."""
        token = request.session.get('oidc_access_token')
        if not token:
            # The user is authenticated to Django but has no OIDC token on the
            # session (e.g. the session was created by another auth backend, or
            # was partially flushed). Previously this raised KeyError and
            # surfaced as a 500; a 401 tells the caller to re-authenticate,
            # which is the only thing that can actually fix it.
            logger.warning("Streamlit token requested but no OIDC access token is on the session")
            return Response(
                {'error': 'No active OIDC session; re-authentication required'},
                status=status.HTTP_401_UNAUTHORIZED,
            )

        payload = {'token': token}

        expires_at = _access_token_expiry(request.session, token)
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

        return Response(payload, status=status.HTTP_200_OK)


    def _check_token_status(self, token: str, user) -> str:
        """Check token status: 'valid', 'refresh', or 'invalid'"""
        try:
            jwt_secret = getattr(settings, 'JWT_SECRET_KEY', settings.SECRET_KEY)
            payload = jwt.decode(token, jwt_secret, algorithms=['HS256'])

            # Verify user matches
            if str(user.id) != payload.get('sub'):
                return 'invalid'

            # Check if revoked
            jti = payload.get('jti')
            if jti:
                cache_key = f"jwt_token:{jti}"
                token_meta = cache.get(cache_key)
                if token_meta and token_meta.get('revoked'):
                    return 'invalid'

            # Check expiration timing
            now = datetime.now(timezone.utc).timestamp()
            exp = payload.get('exp', 0)

            # If expires in less than 1 minute, needs refresh
            if exp - now < 60:
                return 'refresh'

            # If token is still valid for more than 1 minute
            return 'valid'

        except jwt.ExpiredSignatureError:
            return 'invalid'
        except jwt.InvalidTokenError:
            return 'invalid'

    def _generate_new_token(self, user, request, action='generated'):
        """Generate a new JWT token"""
        now = datetime.now(timezone.utc)
        exp_time = now + timedelta(minutes=1)
        jti = str(uuid.uuid4())

        # Get origin and permissions
        origin = request.META.get('HTTP_ORIGIN', request.META.get('HTTP_REFERER', ''))
        permissions = self._get_user_permissions(request)

        payload = {
            'sub': str(user.id),
            'email': getattr(user, 'email', ''),
            'preferred_username': getattr(user, 'username', ''),
            'permissions': permissions,
            'exp': int(exp_time.timestamp()),
            'iat': int(now.timestamp()),
            'nbf': int(now.timestamp()),
            # 'iss': 'lex-backend',
            # 'aud': 'streamlit-iframe',
            # 'jti': jti,
            # 'session_id': request.session.session_key,
            # 'origin': origin,
            # 'token_type': 'streamlit_access'
        }

        jwt_secret = getattr(settings, 'JWT_SECRET_KEY', settings.SECRET_KEY)
        token = jwt.encode(payload, jwt_secret, algorithm='HS256')


        logger.info(f"{action.capitalize()} JWT token for user {user.email} (jti: {jti})")

        return Response({
            'token': token,
            'expires_in': 300,
            'expires_at': exp_time.isoformat(),
            'refresh_interval': 240,  # Refresh after 4 minutes
            'action': action,
            'user': {
                'id': str(user.id),
                'email': payload['email'],
                'username': payload['preferred_username']
            }
        }, status=status.HTTP_200_OK)

    def _get_user_permissions(self, request):
        """Get user permissions"""
        try:
            access_token = None
            auth_header = request.META.get('HTTP_AUTHORIZATION', '')
            if auth_header.startswith('Bearer '):
                access_token = auth_header[7:]

            if access_token:
                from .KeycloakManager import KeycloakManager
                kc_manager = KeycloakManager()
                return kc_manager.get_uma_permissions(access_token)
        except Exception as e:
            logger.warning(f"Failed to get permissions: {e}")
        return []


class StreamlitTokenRevokeView(APIView):
    """Revoke JWT tokens"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Revoke a JWT token"""
        try:
            token = request.data.get('token')
            if not token:
                return Response({'message': 'No token to revoke'}, status=status.HTTP_200_OK)

            # Extract jti without validation (token might be expired)
            try:
                jwt_secret = getattr(settings, 'JWT_SECRET_KEY', settings.SECRET_KEY)
                payload = jwt.decode(
                    token,
                    jwt_secret,
                    algorithms=['HS256'],
                    options={"verify_signature": False, "verify_exp": False, "verify_nbf": False, "verify_aud": False}
                )

                jti = payload.get('jti')
                if jti:
                    cache_key = f"jwt_token:{jti}"
                    token_meta = cache.get(cache_key, {})
                    token_meta['revoked'] = True
                    token_meta['revoked_by'] = request.user.id
                    token_meta['revoked_at'] = datetime.now().isoformat()
                    cache.set(cache_key, token_meta, timeout=600)

                return Response({'message': 'Token revoked successfully'}, status=status.HTTP_200_OK)

            except jwt.DecodeError:
                return Response({'message': 'Invalid token format'}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"Failed to revoke JWT token: {str(e)}")
            return Response({'error': 'Failed to revoke token'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)