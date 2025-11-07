from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response


class UserPermissionsView(APIView):
    """
    Return an array of { action, resource, record? } formatted for ra-rbac.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        perms = getattr(request.user.profile, "uma_permissions", []) or []
        ra_perms = []
        for p in perms:
            for scope in p.get("scopes", []):
                action = "read" if scope == "read" else scope
                ra = {
                    "action": action,
                    "resource": p.get("rsname")
                    .split(".")[-1]
                    .lower(),  # use the last part of the resource name
                }
                if p.get("resource_set_id"):
                    ra["record"] = {"id": p["resource_set_id"]}
                ra_perms.append(ra)
        return Response(ra_perms)