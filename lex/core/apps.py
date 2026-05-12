from lex.lex_app.apps import LexAppConfig


class CoreConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.core'
    verbose_name = 'Core'