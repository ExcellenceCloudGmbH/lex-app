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
