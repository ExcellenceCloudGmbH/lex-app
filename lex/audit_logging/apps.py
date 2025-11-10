from lex.utilities.config.generic_app_config import GenericAppConfig
from lex_app.apps import LexAppConfig


class LoggingConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.audit_logging'
    verbose_name = 'Audit Logging'