import os

from django.contrib.auth.models import User, Group
from sentry_sdk import set_user
from generic_app.rest_api.views.lex_api.LexAPI import get_client_roles

def resolve_user(request, id_token, rbac=True):
    # ask graph if logged in user is in a group /me/memberOf
    # want to see group 6d558e06-309d-4d6c-bb50-54f37a962e40
    # in http://graph.microsoft.com/v1.0/me/memberOf
    # in request._request.headers._store['authorization'] is auth header
    if os.getenv("DEPLOYMENT_ENVIRONMENT"):
        response = get_client_roles()
        client_roles = response['roles']
    set_user({"name": id_token['name'], "email": id_token['email']})
    user, _ = User.objects.get_or_create(username=id_token['sub'])
    user.email = id_token['email']
    user.first_name = id_token["given_name"]
    user.last_name = id_token["family_name"]
    user.roles = []
    if rbac:
        user_roles = id_token['client_roles']
        user.roles = user_roles
        user.save()

        if os.getenv("DEPLOYMENT_ENVIRONMENT"):
            if all(item not in user_roles for item in client_roles):
                return None

            for role in client_roles:
                temp_group, created = Group.objects.get_or_create(name=role)
                if role in user_roles and temp_group not in user.groups.all():
                    user.groups.add(temp_group)

    user.save()

    return user

# Below part is needed when the Memcached cache framework is used
# to save OIDC related key/value pairs

# from django.utils.encoding import smart_str
#
# def _smart_key(key):
#     return smart_str(''.join([c for c in str(key) if ord(c) > 32 and ord(c) != 127]))
#
# def make_key(key, key_prefix, version):
#     "Truncate all keys to 250 or less and remove control characters"
#     return ':'.join([key_prefix, str(version), _smart_key(key)])[:250]
