# lex_app/rest_api/middleware.py

import logging
from lex.api.views.authentication.KeycloakManager import KeycloakManager

# It's good practice to have a dedicated logger for your middleware
logger = logging.getLogger(__name__)

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
        if access_token:
            kc_manager = KeycloakManager()
            try:
                # Fetch all permissions once and attach them to the request.
                # This list will be the single source of truth for the rest of the request.
                permissions = kc_manager.get_uma_permissions(access_token)
                request.user_permissions = permissions if permissions is not None else []
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
            except Exception as e:
                logger.warning(f"Failed to fetch Keycloak userinfo: {e}")

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
