"""``lex schema`` – write the OpenAPI schema of the project API.

Same options as drf-spectacular's ``spectacular`` command (``--file``,
``--validate``, ``--fail-on-warn``, ``--format``), with title, version and the
included paths taken from ``lex_config.SCHEMA``::

    lex schema --validate --fail-on-warn --file openapi.yaml
"""
from drf_spectacular.management.commands.spectacular import Command as SpectacularCommand
from drf_spectacular.settings import patched_settings

from lex.api.schema import custom_settings


class Command(SpectacularCommand):
    help = "Generate the OpenAPI schema of the project API (configured by lex_config.SCHEMA)."

    def handle(self, *args, **options):
        with patched_settings(custom_settings()):
            return super().handle(*args, **options)
