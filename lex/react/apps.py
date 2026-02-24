from django.apps import AppConfig
from pathlib import Path

class ReactConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "lex.react"          # <-- IMPORTANT (not "react")
    path = str(Path(__file__).resolve().parent)  # <-- IMPORTANT
