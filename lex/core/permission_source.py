"""Who decides what a caller may see and change: lex's Python hooks or the database.

A model opts in with ``lex_permission_source = "database"`` (or every model
of the project through ``lex_config.PERMISSION_SOURCE = "database"``; lex's
own models keep their rules unless they set the attribute). For such a model the
**database decides**, typically with PostgreSQL row-level security and the
caller set by ``lex_config.DB_CONTEXT``:

* lex's default ``permission_*`` hooks let every call through — no Keycloak
  scopes are consulted; a model's own overrides still apply;
* lists, single records, history, search, export and downloads use the
  queryset as the database returns it, without a Python check per row;
* an update that reaches no row (the policy hides it from UPDATE) raises
  :class:`RowNotWritable`, answered 404, instead of falling back to an INSERT;
* a policy refusal (SQLSTATE 42501) goes to ``DB_ERROR_MAPPER``.

The mode is only safe when the database really decides, so lex checks on the
first request that every such table has row-level security enabled and that
the role the application runs as is neither a superuser, nor the tables'
owner, nor ``BYPASSRLS`` (:func:`problems`); otherwise it answers 503.
"""
from __future__ import annotations

import logging
import threading
from typing import List

from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError

logger = logging.getLogger(__name__)

SOURCES = ("python", "database")


class RowNotWritable(DatabaseError):
    """An update of an existing row touched no row: the database does not let this caller write it."""


def is_policy_refusal(exc) -> bool:
    """A write the database's row-level security refused (RowNotWritable, or SQLSTATE 42501 from a policy)."""
    if isinstance(exc, RowNotWritable):
        return True
    if not isinstance(exc, DatabaseError):
        return False
    cause = getattr(exc, "__cause__", None)
    code = getattr(cause, "sqlstate", None) or getattr(cause, "pgcode", None)
    return code == "42501" and "row-level security" in str(exc)


def global_source() -> str:
    from lex.core.extensions import get_setting

    source = get_setting("PERMISSION_SOURCE") or "python"
    if source not in SOURCES:
        raise ImproperlyConfigured(f"lex_config.PERMISSION_SOURCE must be one of {SOURCES}, not {source!r}")
    return source


def database_decides(model) -> bool:
    """Whether the database decides for ``model`` (a LexModel class or instance)."""
    from lex.core.models.LexModel import LexModel

    model = model if isinstance(model, type) else type(model)
    if not issubclass(model, LexModel):
        return False
    source = getattr(model, "lex_permission_source", None)
    if source is None:
        # The global key is for the project's models; lex's own (the audit log, ...) keep their rules.
        if _lex_own(model):
            return False
        source = global_source()
    if source not in SOURCES:
        raise ImproperlyConfigured(f"{model.__name__}.lex_permission_source must be one of {SOURCES}, not {source!r}")
    return source == "database"


def _lex_own(model) -> bool:
    module = model.__module__ or ""
    return module.startswith("lex.") and not module.startswith("lex.test_project.")


def database_models() -> list:
    from django.apps import apps

    return [model for model in apps.get_models() if database_decides(model)]


def _tables(model) -> List[str]:
    tables = [model._meta.db_table]
    history = getattr(model, "history", None)
    if history is not None:
        tables.append(history.model._meta.db_table)
        meta = getattr(history.model, "meta_history", None)
        if meta is not None:
            tables.append(meta.model._meta.db_table)
    return tables


def problems(connection=None) -> List[str]:
    """Why the database cannot decide here (empty when it can). PostgreSQL only."""
    from django.db import connection as default_connection

    connection = connection or default_connection
    models = [m for m in database_models() if m._meta.managed and not m._meta.proxy]
    if connection.vendor != "postgresql" or not models:
        return []
    found = []
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_user, rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user")
        role, superuser, bypass = cursor.fetchone()
        if superuser:
            found.append(f"the application runs as {role}, a superuser, which ignores row-level security")
        if bypass:
            found.append(f"the application runs as {role}, which has BYPASSRLS")
        for model in models:
            for table in _tables(model):
                cursor.execute(
                    "SELECT c.relrowsecurity, pg_get_userbyid(c.relowner) FROM pg_class c WHERE c.oid = to_regclass(%s)",
                    [table],
                )
                row = cursor.fetchone()
                if row is None:
                    continue
                enabled, owner = row
                if not enabled:
                    found.append(f"{table} ({model.__name__}) has no row-level security")
                if owner == role:
                    found.append(f"{table} ({model.__name__}) is owned by {role}, so its policies do not apply to it")
    return found


_checked = threading.Event()
_failed: List[str] = []


def reset_check() -> None:
    """Forget the first-request check (tests)."""
    _checked.clear()
    _failed.clear()


class DatabaseDecidesGuardMiddleware:
    """Refuses service (503) when a model lets the database decide but the database cannot."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        import os

        # Test suites that run as the tables' owner switch the check off; never a deployment.
        if os.getenv("LEX_DATABASE_DECIDES_CHECK", "").strip().lower() in ("0", "false", "no", "off"):
            return self.get_response(request)
        if not _checked.is_set():
            try:
                _failed[:] = problems()
            except ImproperlyConfigured:
                raise
            except Exception:  # an unreachable database answers on its own later
                logger.exception("Could not check the database-decides preconditions")
                _failed[:] = []
            _checked.set()
            for problem in _failed:
                logger.error("Database-decides mode refused: %s", problem)
        if _failed:
            from django.http import JsonResponse

            detail = "The service is not configured safely."
            return JsonResponse({"code": "SERVICE_MISCONFIGURED", "detail": detail, "error": detail}, status=503)
        return self.get_response(request)
