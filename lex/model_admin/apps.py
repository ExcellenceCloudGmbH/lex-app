from lex.lex_app.apps import LexAppConfig


class ModelAdminConfig(LexAppConfig):
    """
    Configuration for the model admin app handling custom administrative interface.
    """
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.model_admin'
    verbose_name = 'Model Admin'