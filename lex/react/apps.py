from pathlib import Path

from django.apps import AppConfig


class ReactConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lex.react"          # <-- IMPORTANT (not "react")
    path = str(Path(__file__).resolve().parent)  # <-- IMPORTANT
