from lex.lex_app.apps import LexAppConfig


class SchedulingConfig(LexAppConfig):
    """Scheduled jobs: ``ScheduledJob`` rows and the backend's clock (``lex.scheduling.loop``).

    Model discovery and registration for the UI come from :class:`LexAppConfig`.
    The clock is not started here — ``ready()`` runs in every process — but from
    ``lex_app/asgi.py``, which only the serving backend imports.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "lex.scheduling"
    label = "scheduling"
    verbose_name = "Scheduling"
