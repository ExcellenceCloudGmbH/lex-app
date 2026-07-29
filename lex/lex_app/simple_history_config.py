"""
Configuration for django-simple-history to exclude Django built-in models
and other system models from history tracking.
"""

# Django built-in models that should NOT have history tracking
DJANGO_BUILTIN_MODELS = {
    # Django core models
    'contenttype',
    'permission',
    'group',
    'user',  # Only if you don't want User history - remove if you do
    'session',
    'logentry',
    
    # Django migrations
    'migration',
    
    # Django admin models
    'logentry',
    
    # Django auth models (optional - remove if you want history for these)
    'group_permissions',
    'user_groups',
    'user_user_permissions',
    
    # Django sites framework
    'site',
    
    # Django flatpages
    'flatpage',
    'flatpage_sites',
    
    # Django redirects
    'redirect',
    
    # Django cache framework
    'cache',
    
    # Django sessions
    'session',
    
    # OAuth2 authcodeflow models (if you don't want history for these)
    'blacklistedtoken',
    
    # REST framework models
    'apikey',
    
    # Celery models (if using django-celery-beat or similar)
    'crontabschedule',
    'intervalschedule',
    'periodictask',
    'solarschedule',
    'clockedschedule',
    'periodictasks',
    
    # Django channels (if using)
    'channellayer',
    
    # Simple history's own models
    'historicalrecord',
    
    # Add any other system models you want to exclude
}

# Third-party app models that should NOT have history tracking
THIRD_PARTY_EXCLUDED_MODELS = {
    # Add models from third-party packages that you don't want to track
    'corsheaders',
    'rest_framework',
    'oauth2_authcodeflow',
    'channels',
    'celery',
    'django_db_views',
}

# Your app-specific models that should NOT have history tracking
# This will be populated from Django settings
APP_EXCLUDED_MODELS = set()

def get_excluded_models():
    """
    Get the complete set of model names that should be excluded from history tracking.
    
    Returns:
        set: Set of lowercase model names to exclude from history tracking
    """
    from django.conf import settings
    
    excluded = set()
    
    # Add Django built-in models if auto-exclusion is enabled
    if getattr(settings, 'SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN', True):
        excluded.update(DJANGO_BUILTIN_MODELS)
        excluded.update(THIRD_PARTY_EXCLUDED_MODELS)
    
    # Add app-specific excluded models from settings
    app_excluded = getattr(settings, 'SIMPLE_HISTORY_EXCLUDED_MODELS', [])
    excluded.update(set(model.lower() for model in app_excluded))
    
    # Add the original APP_EXCLUDED_MODELS
    excluded.update(APP_EXCLUDED_MODELS)
    
    return excluded

def should_track_model(model_class):
    """
    Determine if a model should have history tracking enabled.
    
    Args:
        model_class: Django model class to check
        
    Returns:
        bool: True if the model should have history tracking, False otherwise
    """
    from django.conf import settings
    
    model_name = model_class.__name__.lower()
    app_label = model_class._meta.app_label.lower()
    
    # CRITICAL: Never track Historical models themselves (prevents HistoricalHistorical* models)
    if model_class.__name__.startswith('Historical'):
        return False
    
    # Check if model name is in excluded list
    if model_name in get_excluded_models():
        return False
    
    # Check if auto-exclusion is enabled and it's from Django's built-in apps
    if getattr(settings, 'SIMPLE_HISTORY_AUTO_EXCLUDE_BUILTIN', True):
        django_apps = {
            'admin', 'auth', 'contenttypes', 'sessions', 'messages', 
            'staticfiles', 'sites', 'flatpages', 'redirects'
        }
        if app_label in django_apps:
            return False
        
        # Check if it's from excluded third-party apps
        excluded_apps = {
            'oauth2_authcodeflow', 'rest_framework', 'corsheaders', 
            'channels', 'celery', 'django_db_views', 'simple_history'
        }
        if app_label in excluded_apps:
            return False
    
    # Check if model is abstract
    if model_class._meta.abstract:
        return False
    
    # Check if model already has history (avoid double registration)
    if hasattr(model_class, 'history'):
        return False
    
    return True

def get_model_exclusion_reason(model_class):
    """
    Get the reason why a model is excluded from history tracking.
    Useful for debugging and logging.
    
    Args:
        model_class: Django model class to check
        
    Returns:
        str: Reason for exclusion, or None if model should be tracked
    """
    model_name = model_class.__name__.lower()
    app_label = model_class._meta.app_label.lower()
    
    # Check for Historical models first (most important check)
    if model_class.__name__.startswith('Historical'):
        return "Historical model (simple_history generated)"
    
    if model_name in DJANGO_BUILTIN_MODELS:
        return f"Django built-in model: {model_name}"
    
    if model_name in THIRD_PARTY_EXCLUDED_MODELS:
        return f"Third-party excluded model: {model_name}"
    
    if model_name in APP_EXCLUDED_MODELS:
        return f"App-specific excluded model: {model_name}"
    
    django_apps = {
        'admin', 'auth', 'contenttypes', 'sessions', 'messages', 
        'staticfiles', 'sites', 'flatpages', 'redirects'
    }
    if app_label in django_apps:
        return f"Django built-in app: {app_label}"
    
    excluded_apps = {
        'oauth2_authcodeflow', 'rest_framework', 'corsheaders', 
        'channels', 'celery', 'django_db_views', 'simple_history'
    }
    if app_label in excluded_apps:
        return f"Excluded third-party app: {app_label}"
    
    if model_class._meta.abstract:
        return "Abstract model"
    
    if hasattr(model_class, 'history'):
        return "Already has history tracking"
    
    return None  # Should be tracked

# ---------------------------------------------------------------------------
# Migration-only processes
# ---------------------------------------------------------------------------
#
# Registering history doubles the model count twice over: every tracked model
# gains a Level 1 ``Historical<X>`` and a Level 2 ``Meta<Historical<X>>``, each
# carrying the parent's full field set. A process that is only *applying*
# migration files never touches those classes -- ``migrate`` builds its state
# from the migration files themselves, not from the live app registry -- but it
# still pays to construct them at import time, on top of the ProjectState the
# migration executor builds over the same tripled model set.
#
# On a large project that is the difference between a deploy that fits in the
# container's memory and one that is OOM-killed before the first table exists.

#: Management commands that only ever *apply* existing migrations.
_APPLY_ONLY_COMMANDS = {"migrate"}

#: Commands that apply migrations but can also *generate* them first. Safe to
#: skip registration only when generation is explicitly disabled.
#:
#: ``init`` is deliberately NOT here. It applies migrations, but it also syncs
#: Keycloak resources and permissions by enumerating the live app registry
#: (``get_all_django_models`` -> ``app_config.get_models()``). Skipping history
#: registration there would make every ``Historical<X>`` and
#: ``Meta<Historical<X>>`` invisible to that sync, so the history models would
#: silently lose their Keycloak resources. History is load-bearing; nothing that
#: reads the registry for anything other than applying migrations may skip it.
_CONDITIONAL_COMMANDS = {"lex_migrate"}


def is_migration_only_process(argv=None) -> bool:
    """True when this process only applies migrations and needs no history models.

    Deliberately a positive match on a known-safe command line, never a
    heuristic: the cost of a false positive is severe. ``makemigrations`` needs
    every ``Historical<X>`` present in the app registry, because they are built
    at runtime -- if they are missing while autodetection runs, Django sees them
    as deleted models and writes migrations that **drop the history tables**.
    So a command that might generate migrations only qualifies when generation
    is explicitly switched off.

    This never disables history *tracking*: it only avoids constructing the
    history model classes in a short-lived process that applies migration files
    and exits. History tables are created by the migrations themselves, and the
    serving process registers everything as before.

    Set ``LEX_SKIP_MIGRATE_HISTORY=false`` to disable the optimisation entirely.
    """
    import os
    import sys

    override = os.getenv("LEX_SKIP_MIGRATE_HISTORY")
    if override is not None and override.strip().lower() in {"0", "false", "no"}:
        return False

    args = list(sys.argv if argv is None else argv)
    if not args:
        return False

    # Never skip while generating migrations, whatever else is on the line.
    if "makemigrations" in args:
        return False

    if any(arg in _APPLY_ONLY_COMMANDS for arg in args[1:]):
        return True

    if any(arg in _CONDITIONAL_COMMANDS for arg in args[1:]):
        return "--no-makemigrations" in args

    return False
