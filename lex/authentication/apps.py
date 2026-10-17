from lex.lex_app.apps import LexAppConfig


class AuthenticationConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.authentication'
    verbose_name = 'Authentication'

    def ready(self):
        super().ready()
        from lex.authentication.admin import register_api_key_admin

        register_api_key_admin()
