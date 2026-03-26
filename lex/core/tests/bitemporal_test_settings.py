"""
Minimal Django settings for running bitemporal unit tests.

Uses SQLite in-memory, disables all migrations (tests create their
own tables via schema_editor), and includes only the apps needed
by the bitemporal layer.
"""
import os
import sys

# Ensure the lex package root and project root are on sys.path
_LEX_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PROJECT_ROOT = os.environ.get("PROJECT_ROOT", os.getcwd())

for p in (_LEX_ROOT, _PROJECT_ROOT):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("LEX_APP_PACKAGE_ROOT", _LEX_ROOT)
os.environ.setdefault("PROJECT_ROOT", _PROJECT_ROOT)

SECRET_KEY = "test-secret-key-for-bitemporal"
DEBUG = True
USE_TZ = False  # Tests use naive datetimes

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
        "TEST": {"NAME": ":memory:"},
    }
}

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "simple_history",
    "lex.lex_app.apps.LexAppConfig",
]

# Skip ALL migrations — bitemporal tests create tables manually.
MIGRATION_MODULES = {app.split(".")[-1]: None for app in INSTALLED_APPS}

# simple_history settings
SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN = True
