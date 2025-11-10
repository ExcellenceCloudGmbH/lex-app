from lex.utilities.config.generic_app_config import GenericAppConfig
from lex_app.apps import LexAppConfig


class CoreConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.core'
    verbose_name = 'Core'