"""Where things lead: a link names a page function, a declared raw page or a record, never a route string."""

from __future__ import annotations

import dataclasses
import re
import typing
from typing import Any, Callable
from urllib.parse import quote

from lex.lex_app.reflex.ui.pages.parts import Build

_ARGUMENT = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


@dataclasses.dataclass(frozen=True)
class PageInfo:
    """What ``@ui.page`` knows about a page: enough to link to it."""

    name: str
    route: str
    arguments: tuple[str, ...]          # the names in the route's brackets
    params: tuple[str, ...] = ()        # further query parameters the page accepts


#: Words ``ui.link`` and ``ui.button`` take themselves: a page parameter of that name could not be passed.
RESERVED = ("to", "record", "href", "visible", "label", "handler", "tone", "icon")


def info_of(route: str, name: str, params: Any = ()) -> PageInfo:
    """What a link needs to know about the page at ``route``; refuses what a link could not address."""
    if "[[" in route or "[..." in route:
        raise ValueError(f"ui.page({route!r}): only plain route arguments are supported, such as "
                         "\"/things/[key]\"; optional and catch-all arguments are not.")
    arguments, params = tuple(_ARGUMENT.findall(route)), tuple(params)
    taken = [word for word in (*arguments, *params) if word in RESERVED]
    if taken:
        raise ValueError(f"ui.page({route!r}): a page parameter cannot be named {', '.join(taken)}; "
                         "`ui.link` and `ui.button` use that word themselves. Rename it.")
    return PageInfo(name, route, arguments, params)


class ReflexPage:
    """A page written in raw Reflex, declared as somewhere a link may lead (see :func:`reflex_page`)."""

    __slots__ = ("__ui_page__", "__name__")

    def __init__(self, info: PageInfo) -> None:
        self.__ui_page__ = info
        self.__name__ = info.name

    def __repr__(self) -> str:
        return f"ui.reflex_page({self.__ui_page__.route!r})"


def reflex_page(route: str, *, params: Any = ()) -> ReflexPage:
    """A page written in raw Reflex at ``route``, as somewhere a lex.ui link may lead.

    For a project that moves to lex.ui a table at a time: its pages are registered with
    ``app.add_page`` and are not ``@ui.page`` functions yet, but a lex.ui table on one of them
    still links to the others. Pass the result wherever a link takes a page::

        REQUEST = ui.reflex_page("/requests/[req]")

        ui.address(REQUEST, req=row["key"])            # in a @ui.rows handler
        ui.table(State.requests, open=REQUEST, ...)    # every row opens its request

    ``params`` are the query parameters the page reads besides its route's arguments. Declare it
    next to the ``app.add_page`` call that registers the route: nothing else checks that the
    page exists.
    """
    if not isinstance(route, str) or not route.startswith("/"):
        raise ValueError(f"ui.reflex_page() takes the page's route as app.add_page registers it, starting with /, "
                         f"such as \"/requests/[req]\"; got {route!r}.")
    return ReflexPage(info_of(route, route, params))


def page_info(target: Any) -> PageInfo | None:
    return getattr(target, "__ui_page__", None)


def href(ctx: Build, target: Callable[..., Any], values: dict) -> Any:
    """The address of the page ``target`` with ``values``; a str, or a Var when a value is one."""
    info = page_info(target)
    if info is None:
        raise ctx.fail(f"`to=` got {getattr(target, '__name__', target)!r}, which is not a page.",
                       "Pass a function declared with `@ui.page`, or a raw Reflex page declared with "
                       "`ui.reflex_page(\"/route\")`. For an outside site use `href=\"https://...\"`.")
    missing = [name for name in info.arguments if name not in values]
    unknown = [name for name in values if name not in info.arguments and name not in info.params]
    if missing or unknown:
        accepted = ", ".join([*info.arguments, *info.params]) or "none"
        problem = " and ".join(part for part in (
            f"is missing {', '.join(missing)}" if missing else "",
            f"does not accept {', '.join(unknown)}" if unknown else "") if part)
        example = "".join(f", {name}=..." for name in info.arguments)
        raise ctx.fail(f"a link to page `{info.name}` ({info.route}) {problem}.",
                       f"Page `{info.name}` takes: {accepted}. Write `to={info.name}{example}`.")
    path: Any = ""
    rest = info.route
    for name in info.arguments:
        before, _, rest = rest.partition(f"[{name}]")
        path = path + before + piece(ctx, name, values[name])
    path = path + rest
    query = [(name, values[name]) for name in info.params if name in values]
    for index, (name, value) in enumerate(query):
        path = path + ("?" if index == 0 else "&") + name + "=" + piece(ctx, name, value)
    return path


def row_href(ctx: Build, target: Callable[..., Any], row_key: str) -> str:
    """The row-link template of a table whose rows open ``target``: its one argument is the row's key."""
    info = page_info(target)
    if info is None or len(info.arguments) != 1:
        raise ctx.fail(f"`open=` needs a page with exactly one route argument, got {getattr(target, '__name__', target)!r}.",
                       "Declare the page as `@ui.page(\"/things/[key]\")`.")
    return info.route.replace(f"[{info.arguments[0]}]", "{" + row_key + "}")


def piece(ctx: Build, name: str, value: Any, instead: str | None = None) -> Any:
    """``value`` as one piece of an address, encoded so that it stays one piece.

    A value from data must not be able to change the page or add a parameter: a fixed value is
    quoted here, a state value by the browser.
    """
    import reflex as rx

    instead = instead or f"Pass text or a number: `{name}=row.id`, `{name}=7`."
    if isinstance(value, rx.Var):
        kind = typing.get_origin(value._var_type) or value._var_type
        if kind in (bool, list, dict, tuple, set):
            raise ctx.fail(f"the value for `{name}` in a link is a {getattr(kind, '__name__', kind)} from the state.", instead)
        # String(x ?? '') keeps a 0 as "0"; Reflex's own to-text turns it into nothing.
        return rx.Var(f"encodeURIComponent(String({value!s} ?? ''))", _var_data=value._get_all_var_data()).to(str)
    if value is None or isinstance(value, bool) or str(value) in ("", ".", ".."):
        raise ctx.fail(f"the value for `{name}` in a link is {value!r}.", instead)
    return quote(str(value), safe="")


class Address(str):
    """A page's address made by :func:`address`. A table follows only these: a plain string from data
    never becomes a link."""

    __slots__ = ()


def address(page: Callable[..., Any], **values: Any) -> Address:
    """The address of ``page`` (a function declared with ``@ui.page``), with its values, built in Python.

    For rows that lead to different pages: a ``@ui.rows`` handler puts ``ui.address(...)`` in a field
    of each row, and the table's ``open=`` (or a column's) names that field. A value that is ``None``
    or empty leaves the row without a link: ``ui.address`` then returns ``""``.
    """
    info = page_info(page)
    if info is None:
        raise TypeError(f"ui.address() takes a page declared with @ui.page or ui.reflex_page(\"/route\"); "
                        f"got {getattr(page, '__name__', page)!r}")
    missing = [name for name in info.arguments if name not in values]
    unknown = [name for name in values if name not in info.arguments and name not in info.params]
    if missing or unknown:
        accepted = ", ".join([*info.arguments, *info.params]) or "none"
        raise TypeError(f"ui.address({info.name}, ...): page `{info.name}` ({info.route}) takes {accepted}; "
                        f"got {', '.join(values) or 'nothing'}.")
    path, rest = "", info.route
    for name in info.arguments:
        value = values[name]
        if value is None or isinstance(value, bool) or str(value) in ("", ".", ".."):
            return Address("")
        before, _, rest = rest.partition(f"[{name}]")
        path += before + quote(str(value), safe="")
    path += rest
    query = [(name, values[name]) for name in info.params if values.get(name) not in (None, "")]
    for index, (name, value) in enumerate(query):
        path += ("?" if index == 0 else "&") + name + "=" + quote(str(value), safe="")
    return Address(path)
