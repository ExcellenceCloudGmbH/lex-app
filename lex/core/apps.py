from lex.lex_app.apps import LexAppConfig


class CoreConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.core'
    verbose_name = 'Core'

    def ready(self):
        super().ready()
        from django.db.models.signals import post_migrate

        post_migrate.connect(
            restrict_bitemporal_functions, sender=self, dispatch_uid="lex.core.restrict_bitemporal_functions"
        )


def restrict_bitemporal_functions(sender=None, using="default", **kwargs):
    """After every migrate: apply ``lex_config.BITEMPORAL_FUNCTION_ROLES`` (no-op while it is None)."""
    from lex.core.extensions import get_setting

    roles = get_setting("BITEMPORAL_FUNCTION_ROLES")
    if roles is None:
        return
    from lex.core.services.activation_applier import restrict_function_privileges

    restrict_function_privileges(list(roles), using=using)
