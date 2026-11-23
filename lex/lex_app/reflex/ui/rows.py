"""@ui.rows: a handler that returns a table's rows, and the one way lex-app reads them."""

from __future__ import annotations

import asyncio
import contextlib
import functools
import inspect
from typing import Any, Callable

import reflex as rx

from lex.lex_app.reflex.ui import context, guard, workcopy


class RowsError(Exception):
    """A rows handler returned something a table cannot show. The message says what to write."""


class ReadOnly(workcopy.Draft):
    """What a ``@ui.rows`` body sees as ``self``: the state's fields, which it may read but not set."""

    __slots__ = ()

    def __setattr__(self, name: str, value: Any) -> None:
        state_cls = object.__getattribute__(self, "_cls")
        raise AttributeError(f"{state_cls.__name__}.{name}: `@ui.rows` reads; change the state in a "
                             "`@ui.load` or an `@ui.action`.")


def rows(fn: Callable[..., Any] | None = None, *, requires: Any = None) -> Any:
    """A table's rows: a plain function that returns them as a list of dicts.

    lex-app calls it when a table on the page asks -- when the table appears, on each page, sort,
    search and filter change, on refresh, and after a handler of the same state finishes -- as the
    user, off the event loop, on a read-only copy of the state. It searches, filters, sorts and
    pages the rows itself, and sends the browser only the visible page.

    It takes no arguments, or one: ``search``. A rows handler with ``search`` receives the table's
    search text and does the search itself (a ranked search, a search in another system).

    It reads only: setting a field fails the read. The database is reached through ``self.api``,
    or directly when ``requires=`` says what the handler needs, as for every handler. A slow
    source caches where it reads (Django's ``cache.get_or_set``); lex-app keeps nothing.

    ``self.params`` comes from the address and can be anything: pass ``self.user`` to your logic
    so that it returns only what this user may see.
    """

    def decorate(fn: Callable[..., Any]) -> Any:
        from lex.lex_app.reflex.ui import handlers

        handlers._check_plain(fn, "rows")
        if inspect.isgeneratorfunction(fn):
            raise TypeError(f"@ui.rows {fn.__qualname__} returns its rows; it does not yield. "
                            "Return a list of dicts.")
        names = [name for name in inspect.signature(fn).parameters][1:]
        if names not in ([], ["search"]):
            raise TypeError(f"@ui.rows {fn.__qualname__} takes no arguments, or `search` alone; got "
                            f"{', '.join(names)}. Read the page's values from `self.params`.")
        key = fn.__qualname__
        requirements = guard.normalise(requires)

        @functools.wraps(fn)
        def wrapper(self, *args: Any, **kwargs: Any) -> None:
            # A Reflex state turns every public method into an event; this one is read by tables only.
            if context.active() is not None:
                raise handlers._sibling_call(key, fn.__name__)
            raise RuntimeError(f"{key} is a @ui.rows handler: a table reads it (`ui.table({key}, ...)`); "
                               "it is not an event to send.")

        handlers._mark(wrapper, fn, "rows", requires=requirements, search=bool(names))
        return rx.event(wrapper)

    return decorate(fn) if fn is not None else decorate


def is_rows(handler: Any) -> bool:
    """Whether ``handler`` (``State.name``) is a ``@ui.rows`` handler."""
    return getattr(getattr(handler, "fn", None), "__ui_kind__", "") == "rows"


def takes_search(handler: Any) -> bool:
    return bool(handler.fn.__ui_options__.get("search"))


def owner_of(handler: Any) -> type:
    """The state class a rows handler belongs to."""
    return handler.state


async def read(handler: Any, owner: Any, *, search: str = "") -> list:
    """Run the rows handler of ``owner`` (the live state) as the user and return what it returned.

    Raises what the body raised, ``guard``'s refusal when ``requires=`` is not met, or
    :class:`RowsError` when the value is not a list of dicts.
    """
    from lex.lex_app.reflex.ui import handlers

    fn = handler.fn.__ui_fn__
    options = handler.fn.__ui_options__
    key = handler.fn.__ui_key__
    requirements = options.get("requires")
    session, failure = await handlers._session(owner, requirements)
    if failure is not None:
        raise failure
    live = workcopy.raw(owner)
    draft = ReadOnly(type(live), workcopy.snapshot(live))
    inv = context.Invocation(handler=key, kind="rows", session=session, loop=asyncio.get_running_loop(),
                             live=owner, orm=requirements is not None)
    inv.drafts[type(live)] = draft
    args = (search,) if options.get("search") else ()
    value: Any = None
    async with contextlib.aclosing(handlers._drive(inv, "interactive", fn, draft, args, {})) as steps:
        async for step in steps:
            value = step.value
    return checked(key, value)


def checked(key: str, value: Any) -> list:
    """``value`` as a list of dicts, or a :class:`RowsError` that says what was returned."""
    if not isinstance(value, (list, tuple)):
        raise RowsError(f"{key} returned {type(value).__name__}; a @ui.rows handler returns a list of dicts.")
    out = []
    for index, row in enumerate(value):
        if not isinstance(row, dict):
            if hasattr(row, "_asdict"):
                row = row._asdict()
            elif hasattr(row, "keys") and hasattr(row, "__getitem__"):
                row = {k: row[k] for k in row.keys()}
            else:
                raise RowsError(f"{key} returned {type(row).__name__} as row {index}; each row is a dict "
                                "of field name to value.")
        out.append(row)
    return out


def describe(exc: BaseException, key: str, user: str) -> str:
    """The message a table shows for a failed read (logged once, with a reference)."""
    from lex.lex_app.reflex.ui import handlers

    if isinstance(exc, RowsError):
        exc = RuntimeError(str(exc))
    return handlers._classify(key, exc, user=user).message
