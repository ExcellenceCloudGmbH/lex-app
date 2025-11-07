import requests
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from lex.lex_app import settings


class CurrentUser(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        token = request.session.get("oidc_access_token")
        if not token:
            print("CurrentUser: No access token found in session.")
            return Response({"detail": "Unauthorized"}, status=status.HTTP_401_UNAUTHORIZED)

        # call the OIDC userinfo endpoint
        user_response = requests.get(
            settings.OIDC_OP_USER_ENDPOINT,
            headers={"Authorization": f"Bearer {token}"},
            verify=getattr(settings, "OIDC_VERIFY_SSL", True),
            timeout=getattr(settings, "OIDC_TIMEOUT", None),
            proxies=getattr(settings, "OIDC_PROXY", None),
        )

        # if the upstream returned any error (401, 403, 500, ...)
        if not user_response.ok:
            # try to parse JSON error payload, or fall back to text
            try:
                data = user_response.json()
            except ValueError:
                data = {"detail": user_response.text}
            return Response(data, status=user_response.status_code)

        # otherwise, return the successful payload
        return Response(user_response.json(), status=status.HTTP_200_OK)