SECRET_KEY = "test-secret-key"
DEBUG = True
USE_TZ = True
ROOT_URLCONF = "lex.process_admin.tests.urls"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

INSTALLED_APPS = [
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "rest_framework",
    "rest_framework_api_key",
]

API_KEY_CUSTOM_HEADER = "HTTP_API_KEY"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}
