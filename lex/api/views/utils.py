from lex.api.utils.api_key_requests import get_api_key_request_identity


def get_user_name(request):
    if "JIRA" in getattr(request, "headers", {}):
        return "Created by Jira"

    api_key_identity = get_api_key_request_identity(request)
    if api_key_identity:
        return api_key_identity.api_key_name

    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        name = auth.get("name")
        subject = auth.get("sub")
        if name and subject:
            return f"{name} ({subject})"
        if name:
            return name
        if subject:
            return subject

    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return str(user)

    return "Technical User"


def get_user_email(request):
    if "JIRA" in getattr(request, "headers", {}):
        return "jira@mail.com"

    api_key_identity = get_api_key_request_identity(request)
    if api_key_identity:
        return api_key_identity.email or "technical-user@local"

    auth = getattr(request, "auth", None)
    if isinstance(auth, dict):
        email = auth.get("email")
        if email:
            return email

    user = getattr(request, "user", None)
    if getattr(user, "is_authenticated", False):
        return getattr(user, "email", "") or "technical-user@local"

    return "technical-user@local"
