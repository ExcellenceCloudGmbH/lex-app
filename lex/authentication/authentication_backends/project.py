from rest_framework.authentication import BaseAuthentication


class LexProjectAuthentication(BaseAuthentication):
    """Runs the project's ``lex_config.AUTHENTICATION_CLASSES`` before lex's own.

    lex owns ``REST_FRAMEWORK["DEFAULT_AUTHENTICATION_CLASSES"]``; a project adds
    its classes through ``lex_config.py`` instead of overriding the setting. The
    first configured class that returns a ``(user, auth)`` tuple wins; with
    nothing configured this class returns ``None`` and DRF moves on.
    """

    def _authenticators(self):
        from lex.core.extensions import get_import_list

        return [cls() for cls in get_import_list("AUTHENTICATION_CLASSES")]

    def authenticate(self, request):
        for authenticator in self._authenticators():
            result = authenticator.authenticate(request)
            if result is not None:
                request._lex_project_authenticator = authenticator
                return result
        return None

    def authenticate_header(self, request):
        for authenticator in self._authenticators():
            header = authenticator.authenticate_header(request)
            if header:
                return header
        return None
