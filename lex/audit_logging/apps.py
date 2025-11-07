from lex.utilities.config.generic_app_config import GenericAppConfig


class LoggingConfig(GenericAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.audit_logging'
    verbose_name = 'Audit Logging'