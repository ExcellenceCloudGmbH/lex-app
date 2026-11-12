from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from lex.authentication.identity import user_payload


class CurrentUser(APIView):
    """``GET api/user/``: who the signed-in user is.

    Answered from the validated sign-in (``request.oidc``: the ID token's profile claims and the
    access token's client roles) -- no Keycloak call, so a page load costs nothing upstream and a
    Keycloak hiccup cannot fail it. The shape is the one Keycloak's userinfo answered before
    (``sub``, ``email``, ``name``, ``preferred_username``, ``given_name``, ``family_name``, ...)
    plus ``client_roles``.

    Per the HTTP auth contract v2: no sign-in answers 401 (``not_authenticated``, with
    ``WWW-Authenticate``), an ended sign-in 401 ``session_expired``, and an auth backend that
    cannot refresh the sign-in 503 ``auth_unavailable`` -- the last two from the OIDC session
    middleware, before this view runs.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        response = Response(user_payload(request), status=status.HTTP_200_OK)
        response["Cache-Control"] = "no-store"
        return response
