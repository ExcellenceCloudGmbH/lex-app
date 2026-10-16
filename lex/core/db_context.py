"""Database context per caller — for databases that decide visibility themselves.

Some projects enforce access in PostgreSQL (row-level security), which needs
to know who is asking: ``SET LOCAL app.user_id = …`` at the start of each
transaction. Within a request that is easy; lex also runs work elsewhere —
Celery tasks, the calculation thread pool, ``transaction.on_commit``
callbacks — where nobody would set it, so the database would see an
anonymous caller.

A project registers one function::

    # lex_config.py
    DB_CONTEXT = "myproject.db.set_db_user"

    # myproject/db.py
    def set_db_user(user, *, local):
        with connection.cursor() as cursor:
            cursor.execute("SELECT set_config('app.user_id', %s, %s)",
                           ["" if user is None else str(user.pk), local])

and lex calls it:

* for every request, inside one transaction around the rest of the
  middleware chain, with ``local=True`` (so the setting ends with it);
* at the start of each Celery task and calculation thread, with the user
  who started the work and ``local=False``, and with ``None`` at the end so a
  pooled connection does not keep it;
* inside every ``on_commit`` callback lex registers, in a fresh transaction.

Without ``DB_CONTEXT`` none of this happens and transactions behave as
before.
"""
from __future__ import annotations

import contextlib
import logging
from typing import Any, Callable, Iterator, Optional

from django.db import transaction

from lex.core.extensions import get_hook

logger = logging.getLogger(__name__)


def hook() -> Optional[Callable[..., Any]]:
    return get_hook("DB_CONTEXT")


def _authenticated(user):
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    return user


def apply(user, *, local: bool = True) -> None:
    """Tell the database who is asking (no-op without DB_CONTEXT)."""
    fn = hook()
    if fn is not None:
        fn(_authenticated(user), local=local)


def user_from_operation_context(context: Optional[dict] = None):
    """The user of the operation that started the current work, if known."""
    if context is None:
        from lex.api.utils.Context import operation_context

        context = operation_context.get() or {}
    request = context.get("request_obj")
    if isinstance(request, dict):
        return request.get("user")
    return getattr(request, "user", None)


@contextlib.contextmanager
def for_background(user) -> Iterator[None]:
    """Session-level context for work outside a request; reset afterwards."""
    fn = hook()
    if fn is None:
        yield
        return
    fn(_authenticated(user), local=False)
    try:
        yield
    finally:
        try:
            fn(None, local=False)
        except Exception:  # pragma: no cover - connection already gone
            logger.exception("Resetting the database context failed")


def on_commit(callback: Callable[[], Any], using: Optional[str] = None) -> None:
    """``transaction.on_commit`` that re-applies the caller's database context.

    The callback runs after the request's transaction ended, where a
    ``SET LOCAL`` no longer holds; it gets a fresh transaction with the same
    user. Without DB_CONTEXT this is plain ``transaction.on_commit``.
    """
    if hook() is None:
        transaction.on_commit(callback, using=using)
        return
    user = user_from_operation_context()

    def run():
        with transaction.atomic(using=using):
            apply(user, local=True)
            callback()

    transaction.on_commit(run, using=using)


class LexDbContextMiddleware:
    """One transaction per request with the caller's database context (DB_CONTEXT only)."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if hook() is None:
            return self.get_response(request)
        with transaction.atomic():
            apply(getattr(request, "user", None), local=True)
            return self.get_response(request)
