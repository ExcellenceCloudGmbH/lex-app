from keycloak import KeycloakOpenID


def sync_user_permissions(user, access_token):
    """
    Fetch UMA permissions from Keycloak and save them to the user's profile.
    """
    if not access_token or not getattr(user, "profile", None):
        return

    kc = KeycloakOpenID(
        server_url="https://exc-testing.com",
        realm_name="lex",
        client_id="LEX_LOCAL_ENV",
        client_secret_key="O1dT6TEXjsQWbRlzVxjwfUnNHPnwDmMF",
        verify=False,
    )
    uma_perms = kc.uma_permissions(token=access_token)
    user.profile.uma_permissions = uma_perms
    user.profile.save()
