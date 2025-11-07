from lex.utilities.config.generic_app_config import GenericAppConfig


class AuthenticationConfig(GenericAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.authentication'
    verbose_name = 'Authentication'