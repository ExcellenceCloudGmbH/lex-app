"""The Django ORM, from Reflex event handlers.

Reflex runs every event handler -- sync or async -- on its event loop, and
Django refuses to run its synchronous ORM there: a query made from the loop
would stall every other user's events while it waited on the database, so
Django raises ``SynchronousOnlyOperation`` ("You cannot call this from an async
context") instead. Streamlit never meets this, because a Streamlit script runs
on a thread of its own. It is the one difference in how a dashboard reaches the
database, and there are two ways across it, both Django's own:

* Django's async ORM, for queries: ``await Fund.objects.acount()``,
  ``await Fund.objects.aget(pk=pk)``, ``async for fund in Fund.objects.all()``.
* :func:`run_orm` (or the :func:`orm` decorator), for anything synchronous --
  a model method, ``save()`` and its lifecycle hooks, a calculation, a queryset
  you want back as a list. It runs the code on Django's thread-sensitive
  executor, the same thread the async ORM uses.

What neither gives you is what a Django request gets for free: connection
management. Django retires a connection that outlived ``CONN_MAX_AGE`` or that
the database dropped at the start and end of every request. Nothing does that in
a long-running Reflex worker, so a single dropped connection would fail every
later query until the process restarted. :func:`run_orm` and
:class:`DjangoConnectionMiddleware` put it back -- around each unit of work, and
around each Reflex event.
"""

from __future__ import annotations

import functools
import os
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from asgiref.sync import sync_to_async
from reflex.middleware import Middleware

if TYPE_CHECKING:
    from reflex.app import App
    from reflex.state import BaseState, StateUpdate
    from reflex_base.event import Event

T = TypeVar("T")

_TRUTHY = {"1", "true", "yes", "y", "on"}


def close_stale_connections() -> None:
    """Retire this thread's unusable or expired connections, as a request would.

    ``django.db.close_old_connections`` with one exception: a connection inside
    an ``atomic`` block is left alone. Closing it would discard the transaction
    of whoever opened it -- in a test, the test's own. A Django request never
    meets that case; code running between two Reflex events can.
    """
    from django.db import connections

    for connection in connections.all(initialized_only=True):
        if not connection.in_atomic_block:
            connection.close_if_unusable_or_obsolete()


def _with_fresh_connections(fn: Callable[..., T]) -> Callable[..., T]:
    """Wrap ``fn`` in the connection handling Django gives a request."""

    @functools.wraps(fn)
    def run(*args: Any, **kwargs: Any) -> T:
        close_stale_connections()
        try:
            return fn(*args, **kwargs)
        finally:
            close_stale_connections()

    return run


async def run_orm(fn: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
    """Run synchronous Django code from a Reflex event handler; return its result.

    ``fn`` runs on Django's thread-sensitive executor -- the thread Django's own
    async ORM uses -- with stale connections retired before and after, so it can
    do anything a view could: query, save, call model methods, open a
    transaction. Keep a transaction inside ``fn``: one unit of work, one call.

    Example::

        class FundDashboard(rx.State):
            names: list[str] = []

            @rx.event
            async def load(self):
                self.names = await run_orm(
                    lambda: list(Fund.objects.values_list("name", flat=True))
                )

    Assign the result to the state in the handler rather than touching ``self``
    inside ``fn``: ``fn`` runs on another thread, and the state belongs to the
    event.
    """
    return await sync_to_async(_with_fresh_connections(fn), thread_sensitive=True)(
        *args, **kwargs
    )


def orm(fn: Callable[..., T]) -> Callable[..., Awaitable[T]]:
    """Decorator form of :func:`run_orm`: a synchronous function, made awaitable.

    Example::

        @orm
        def fund_totals() -> list[dict]:
            return list(Fund.objects.values("name").annotate(total=Sum("amount")))


        class FundDashboard(rx.State):
            totals: list[dict] = []

            @rx.event
            async def load(self):
                self.totals = await fund_totals()
    """

    @functools.wraps(fn)
    async def awaitable(*args: Any, **kwargs: Any) -> T:
        return await run_orm(fn, *args, **kwargs)

    return awaitable


def _sync_orm_allowed_on_loop() -> bool:
    """Whether ``DJANGO_ALLOW_ASYNC_UNSAFE`` lets sync ORM calls run on the loop."""
    return (os.getenv("DJANGO_ALLOW_ASYNC_UNSAFE") or "").strip().lower() in _TRUTHY


class DjangoConnectionMiddleware(Middleware):
    """Give every Reflex event the connection handling of a Django request.

    Django's async ORM (``acount``, ``aget``, ``async for`` ...) runs its queries
    on the thread-sensitive executor, and nothing ever retires the connection it
    opens there. This does, before and after each event -- the Reflex
    equivalent of the ``request_started`` / ``request_finished`` handlers.

    When ``DJANGO_ALLOW_ASYNC_UNSAFE`` is set, synchronous ORM calls run on the
    event loop's own thread instead, so that thread's connection is retired
    after each event too.
    """

    async def preprocess(
        self, app: "App", state: "BaseState", event: "Event"
    ) -> "StateUpdate | None":
        """Retire stale connections before the event runs."""
        await sync_to_async(close_stale_connections, thread_sensitive=True)()
        return None

    async def postprocess(
        self,
        app: "App",
        state: "BaseState",
        event: "Event",
        update: "StateUpdate",
    ) -> "StateUpdate":
        """Retire stale connections once the event has run."""
        await sync_to_async(close_stale_connections, thread_sensitive=True)()
        if _sync_orm_allowed_on_loop():
            close_stale_connections()
        return update
