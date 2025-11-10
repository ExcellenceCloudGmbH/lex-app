from lex.utilities.config.generic_app_config import GenericAppConfig
from lex_app.apps import LexAppConfig


class ProcessAdminConfig(LexAppConfig):
    """
    Configuration for the process admin app handling custom administrative interface.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.process_admin'
    verbose_name = 'Process Admin'