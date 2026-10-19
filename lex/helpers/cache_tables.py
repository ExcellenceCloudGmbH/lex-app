# helpers/cache_tables.py
from django.conf import settings
from django.core.management import call_command
from django.db import connections, DEFAULT_DB_ALIAS

def ensure_cache_table(database: str = DEFAULT_DB_ALIAS) -> bool:
    """
    Ensure the database cache backend table exists.
    Returns True if table existed or was created, False if creation failed.
    """
    # Only relevant for DatabaseCache backend
    cache_backend = settings.CACHES.get("default", {}).get("BACKEND", "")
    if "DatabaseCache" not in cache_backend:
        return True

    table_name = settings.CACHES["default"].get("LOCATION", "")
    if not table_name:
        return True  # nothing to do

    conn = connections[database]
    introspector = conn.introspection
    with conn.cursor() as cursor:
        existing = introspector.table_names(cursor)

    if table_name in existing:
        return True

    # create the cache table; do NOT pass `interactive`
    call_command("createcachetable", database=database, verbosity=1)
    # Verify creation
    conn.close()  # refresh introspection cache across some backends
    conn = connections[database]
    with conn.cursor() as cursor:
        existing = conn.introspection.table_names(cursor)
    return table_name in existing


def create_cache_tables(database: str = DEFAULT_DB_ALIAS) -> None:
    """Create every missing ``DatabaseCache`` table (``default_cache``, ``oidc_cache``).

    Idempotent: ``createcachetable`` skips tables that exist. A fresh database
    used to need a manual ``createcachetable`` before the first start, or the
    initial-data load failed with ``relation "default_cache" does not exist``.
    Runs after every ``migrate`` (``post_migrate``) and before the initial-data
    load. Never raises: a failure is logged and the caller carries on.
    """
    import logging

    if not any("DatabaseCache" in str(cache.get("BACKEND", "")) for cache in settings.CACHES.values()):
        return
    try:
        call_command("createcachetable", database=database, verbosity=0)
    except Exception:
        logging.getLogger(__name__).exception("createcachetable failed on database %r", database)


def create_cache_tables_after_migrate(sender=None, using: str = DEFAULT_DB_ALIAS, **kwargs) -> None:
    """``post_migrate`` receiver: the cache tables exist once ``migrate`` has run."""
    create_cache_tables(using)
