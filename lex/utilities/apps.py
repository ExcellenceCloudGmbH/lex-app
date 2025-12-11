from lex.utilities.config.generic_app_config import GenericAppConfig
from lex_app.apps import LexAppConfig


class UtilitiesConfig(LexAppConfig):
    """
    Configuration for the utilities app containing shared utilities, decorators, and helper functions.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.utilities'
    verbose_name = 'Utilities'