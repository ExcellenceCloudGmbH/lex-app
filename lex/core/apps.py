from lex.lex_app.apps import LexAppConfig


class CoreConfig(LexAppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'lex.core'
    verbose_name = 'Core'

    def ready(self):
        super().ready()
        from django.db.models.signals import post_migrate

        from lex.helpers.cache_tables import create_cache_tables_after_migrate

        # A fresh database gets its cache tables with every migrate, so the
        # initial-data load no longer fails on a missing "default_cache".
        post_migrate.connect(
            create_cache_tables_after_migrate,
            sender=self,
            dispatch_uid="lex.core.create_cache_tables_after_migrate",
        )
