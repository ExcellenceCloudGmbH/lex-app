from lex.lex_app.apps import LexAppConfig


class ApiConfig(LexAppConfig):
    """
    Configuration for the API app handling REST API endpoints and external communication.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.api'
    verbose_name = 'API'

    def ready(self):
        super().ready()
        # Register OpenAPI descriptions of lex's authentication classes.
        import lex.api.schema_extensions  # noqa: F401
