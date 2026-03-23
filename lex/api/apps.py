from lex.lex_app.apps import LexAppConfig
from lex.utilities.config.generic_app_config import GenericAppConfig


class ApiConfig(LexAppConfig):
    """
    Configuration for the API app handling REST API endpoints and external communication.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.api'
    verbose_name = 'API'