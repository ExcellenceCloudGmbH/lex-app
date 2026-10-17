# lex_app/rest_api/middleware.py

import base64
import hashlib
import json
import logging
import os
import threading
import time

from django.core.cache import caches

from lex.api.views.authentication.KeycloakManager import KeycloakManager

# It's good practice to have a dedicated logger for your middleware
logger = logging.getLogger(__name__)

_manager = None
_manager_factory = None
_manager_lock = threading.Lock()


def _shared_manager():
    """One KeycloakManager per process instead of one per request.

    Building a manager opens admin/UMA/OIDC clients and resolves the client's
    uuid through the admin API — work that was repeated on every request.
    The configuration comes from the environment and does not change at
    runtime. A failed initialisation is not kept, so the next request retries.
    """
    global _manager, _manager_factory
    factory = KeycloakManager  # looked up per call, so a replaced class (tests, plugins) takes effect
    if _manager is None or _manager_factory is not factory:
        with _manager_lock:
            if _manager is None or _manager_factory is not factory:
                _manager = factory()
                _manager_factory = factory
    return _manager


def _reset_shared_manager():
    """Forget the shared manager (tests)."""
    global _manager, _manager_factory
    _manager = None
    _manager_factory = None


def _cache_seconds() -> int:
    try:
        return max(0, int(os.getenv("LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS", "0") or 0))
    except ValueError:
        return 0


def _token_seconds_left(token: str):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        exp = json.loads(base64.urlsafe_b64decode(payload)).get("exp")
        return int(exp - time.time()) if exp else None
    except Exception:
        return None


def _cache_key(token: str) -> str:
    return "lex:kc-perms:" + hashlib.sha256(token.encode()).hexdigest()


def _cache_get(token: str):
    if _cache_seconds() <= 0:
        return None
    try:
        return caches["oidc"].get(_cache_key(token))
    except Exception:
        return None


def _cache_set(token: str, permissions, userinfo) -> None:
    """Cache permissions and userinfo for LEX_KEYCLOAK_PERMISSION_CACHE_SECONDS, never past the token's expiry."""
    seconds = _cache_seconds()
    if seconds <= 0:
        return
    left = _token_seconds_left(token)
    if left is not None:
        seconds = min(seconds, left)
    if seconds <= 0:
        return
    try:
        caches["oidc"].set(_cache_key(token), (list(permissions or []), dict(userinfo or {})), seconds)
    except Exception:
        logger.warning("Could not cache Keycloak permissions", exc_info=True)

class KeycloakPermissionsMiddleware:
    """
    This middleware is responsible for fetching a user's complete set of UMA (User-Managed Access)
    permissions from Keycloak once per authenticated request.

    It attaches the list of permissions to the `request` object as `request.user_permissions`,
    and user profile data as `request.userinfo`/`request.client_roles`.
    This creates a single, consistent source of truth for permissions that can be used
    by DRF Permission classes, filters, and serializers throughout the request lifecycle,
    avoiding redundant API calls to Keycloak.

    **Installation:**
    Add this middleware to your `settings.py` file in the `MIDDLEWARE` list.
    It should be placed *after* Django's `AuthenticationMiddleware` and `SessionMiddleware`
    to ensure that `request.user` and `request.session` are available.

    Example `settings.py`:
    ```
    MIDDLEWARE = [
        # ... other middleware
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        # ... other middleware
        'lex_app.rest_api.middleware.KeycloakPermissionsMiddleware', # Add this line
    ]
    ```
    """
    def __init__(self, get_response):
        """
        Initializes the middleware. This is called once by the Django server at startup.
        """
        self.get_response = get_response

    def __call__(self, request):
        """
        This method is called for each request. It processes the request before it
        reaches the view and other middleware.
        """
        # First, clean up any None tokens to prevent JWT parsing errors
        # self.cleanup_invalid_tokens(request)
        
        # Always provide defaults so downstream code can safely access these attributes.
        request.user_permissions = []
        request.userinfo = {}
        request.client_roles = []

        access_token = request.session.get("oidc_access_token")

        # Fallback: extract token from Authorization header for Bearer token requests
        if not access_token:
            auth_header = request.META.get("HTTP_AUTHORIZATION", "")
            if auth_header.startswith("Bearer "):
                access_token = auth_header[7:].strip()

        if not access_token:
            # A scoped API key brings its own scopes (lex.authentication.LexAPIKey).
            from lex.api.utils.api_key_requests import get_scoped_api_key, scoped_key_permissions

            scoped_key = get_scoped_api_key(request)
            if scoped_key is not None:
                request.user_permissions = scoped_key_permissions(scoped_key)

        if access_token:
            cached = _cache_get(access_token)
            if cached is not None:
                request.user_permissions, request.userinfo = cached
                request.client_roles = self._extract_client_roles(request.userinfo)
                return self.get_response(request)

            kc_manager = _shared_manager()
            fetched_permissions = fetched_userinfo = False
            try:
                # Fetch all permissions once and attach them to the request.
                # This list will be the single source of truth for the rest of the request.
                permissions = kc_manager.get_uma_permissions(access_token)
                request.user_permissions = permissions if permissions is not None else []
                fetched_permissions = permissions is not None
            except Exception as e:
                # Log any unexpected errors during permission fetching but don't crash the request.
                # Default to no permissions in case of an error.
                logger.error(f"Failed to fetch Keycloak UMA permissions: {e}")

            try:
                if kc_manager.oidc:
                    userinfo = kc_manager.oidc.userinfo(access_token)
                    if isinstance(userinfo, dict):
                        request.userinfo = userinfo
                        request.client_roles = self._extract_client_roles(userinfo)
                        fetched_userinfo = True
            except Exception as e:
                logger.warning(f"Failed to fetch Keycloak userinfo: {e}")

            if fetched_permissions and fetched_userinfo:
                _cache_set(access_token, request.user_permissions, request.userinfo)

        # Pass control to the next middleware or the view.
        response = self.get_response(request)

        return response

    @staticmethod
    def _extract_client_roles(userinfo):
        raw_roles = userinfo.get("client_roles")
        if not raw_roles:
            return []
        if isinstance(raw_roles, str):
            return [raw_roles]
        if isinstance(raw_roles, (list, tuple, set, frozenset)):
            return [role for role in raw_roles if isinstance(role, str)]
        if isinstance(raw_roles, dict):
            roles = []
            for value in raw_roles.values():
                if isinstance(value, str):
                    roles.append(value)
                elif isinstance(value, (list, tuple, set, frozenset)):
                    roles.extend([role for role in value if isinstance(role, str)])
            return roles
        return []

    def cleanup_invalid_tokens(self, request):
        """Remove None or empty tokens from session to prevent JWT parsing errors."""
        session_changed = False
        
        # List of token keys that should not be None
        token_keys = [
            'oidc_id_token',
            'oidc_access_token', 
            'oidc_refresh_token',
        ]
        
        for key in token_keys:
            if key in request.session:
                token_value = request.session[key]
                if token_value is None or token_value == '' or not isinstance(token_value, str):
                    logger.warning(f"Removing invalid token from session: {key} = {repr(token_value)}")
                    del request.session[key]
                    session_changed = True
        
        # If we removed any tokens, also clean up related session data
        if session_changed:
            related_keys = [
                'oidc_access_expires_at',
                'oidc_expires_at',
                'oidc_logout_state',
            ]
            
            for key in related_keys:
                if key in request.session:
                    logger.debug(f"Removing related session data: {key}")
                    del request.session[key]
            
            request.session.save()
            logger.info("Cleaned up invalid tokens from session")
