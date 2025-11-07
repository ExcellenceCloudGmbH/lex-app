from lex.utilities.config.generic_app_config import GenericAppConfig


class UtilitiesConfig(GenericAppConfig):
    """
    Configuration for the utilities app containing shared utilities, decorators, and helper functions.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.utilities'
    verbose_name = 'Utilities'