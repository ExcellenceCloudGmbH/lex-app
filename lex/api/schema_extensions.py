"""drf-spectacular descriptions of lex's authentication classes.

Imported once at startup (``ApiConfig.ready``) so generated schemas list the
security schemes lex accepts. Projects describe their own classes the same way.
"""
from drf_spectacular.extensions import OpenApiAuthenticationExtension


class BearerMiddlewareAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = (
        "lex.authentication.authentication_backends.BearerMiddlewareAuthentication."
        "BearerMiddlewareAuthentication"
    )
    name = "bearerAuth"

    def get_security_definition(self, auto_schema):
        return {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}


class LexProjectAuthenticationScheme(OpenApiAuthenticationExtension):
    """Describes the project classes behind ``LexProjectAuthentication``.

    Each class in ``lex_config.AUTHENTICATION_CLASSES`` that has its own
    ``OpenApiAuthenticationExtension`` contributes its scheme; with none
    configured this adds nothing to the schema.
    """

    target_class = "lex.authentication.authentication_backends.project.LexProjectAuthentication"
    name = []

    def __init__(self, target):
        super().__init__(target)
        from lex.core.extensions import get_import_list

        self._delegates = []
        for cls in get_import_list("AUTHENTICATION_CLASSES"):
            scheme = OpenApiAuthenticationExtension.get_match(cls())
            if scheme is not None:
                self._delegates.append(scheme)
        self.name = []
        for scheme in self._delegates:
            self.name.extend([scheme.name] if isinstance(scheme.name, str) else list(scheme.name))

    def get_security_requirement(self, auto_schema):
        requirements = []
        for scheme in self._delegates:
            requirement = scheme.get_security_requirement(auto_schema)
            if requirement is None:
                continue
            requirements.extend([requirement] if isinstance(requirement, dict) else requirement)
        return requirements

    def get_security_definition(self, auto_schema):
        definitions = []
        for scheme in self._delegates:
            definition = scheme.get_security_definition(auto_schema)
            definitions.extend([definition] if isinstance(scheme.name, str) else list(definition))
        return definitions
