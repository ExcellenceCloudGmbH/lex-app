from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView


class UserPermissionsView(APIView):
    """
    Return an array of { action, resource, record? } formatted for ra-rbac.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, format=None):
        perms = getattr(request, "user_permissions", None)
        if perms is None:
            perms = getattr(request.user.profile, "uma_permissions", []) or []
        ra_perms = []
        for p in perms:
            resource_name = p.get("rsname")
            if not resource_name:
                continue
            for scope in p.get("scopes", []):
                action = "read" if scope == "read" else scope
                ra = {
                    "action": action,
                    "resource": resource_name.split(".")[-1].lower(),
                }
                if p.get("resource_set_id"):
                    ra["record"] = {"id": p["resource_set_id"]}
                ra_perms.append(ra)
        return Response(ra_perms)
