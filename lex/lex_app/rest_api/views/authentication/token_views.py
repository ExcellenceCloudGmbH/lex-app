from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from datetime import datetime, timezone, timedelta
import jwt
import uuid
from django.conf import settings
from django.core.cache import cache
import logging

logger = logging.getLogger(__name__)


class StreamlitTokenView(APIView):
    """Smart JWT token endpoint - gets new token, keeps valid ones, or refreshes expiring ones"""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        """Get or refresh JWT token based on current token state"""


            # No token provided or token invalid - generate new one
        return Response({
            'token': request.session['oidc_access_token'],

        }, status=status.HTTP_200_OK)


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
