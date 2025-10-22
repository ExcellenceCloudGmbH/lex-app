# lex_app/rest_api/middleware.py

import logging
from lex.lex_app.rest_api.views.authentication.KeycloakManager import KeycloakManager

# It's good practice to have a dedicated logger for your middleware
logger = logging.getLogger(__name__)

class KeycloakPermissionsMiddleware:
    """
    This middleware is responsible for fetching a user's complete set of UMA (User-Managed Access)
    permissions from Keycloak once per authenticated request.

    It attaches the list of permissions to the `request` object as `request.user_permissions`.
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
        # We only need to fetch permissions for authenticated users who have a Keycloak token.
        # The `hasattr` check provides a safeguard in case the user object is not standard.
        if (hasattr(request, "user") and
                request.user.is_authenticated and
                "oidc_access_token" in request.session):

            access_token = request.session.get("oidc_access_token")
            if access_token:
                try:
                    # Fetch all permissions once and attach them to the request.
                    # This list will be the single source of truth for the rest of the request.
                    kc_manager = KeycloakManager()
                    permissions = kc_manager.get_uma_permissions(access_token)
                    request.user_permissions = permissions if permissions is not None else []
                except Exception as e:
                    # Log any unexpected errors during permission fetching but don't crash the request.
                    # Default to no permissions in case of an error.
                    logger.error(f"Failed to fetch Keycloak UMA permissions: {e}")
                    request.user_permissions = []
            else:
                # Handle the case where the token is present but empty.
                request.user_permissions = []
        else:
            # For anonymous users or requests without a token, ensure the attribute
            # always exists as an empty list. This prevents `AttributeError` downstream.
            request.user_permissions = []

        # Pass control to the next middleware or the view.
        response = self.get_response(request)

        return response
