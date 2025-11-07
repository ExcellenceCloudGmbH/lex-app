from lex.utilities.config.generic_app_config import GenericAppConfig


class CoreConfig(GenericAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.core'
    verbose_name = 'Core'