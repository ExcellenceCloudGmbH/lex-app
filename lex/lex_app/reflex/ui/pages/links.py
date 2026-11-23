"""Where things lead: a link names a page function or a record, never a route string."""

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


def page_info(target: Any) -> PageInfo | None:
    return getattr(target, "__ui_page__", None)


def href(ctx: Build, target: Callable[..., Any], values: dict) -> Any:
    """The address of the page ``target`` with ``values``; a str, or a Var when a value is one."""
    info = page_info(target)
    if info is None:
        raise ctx.fail(f"`to=` got {getattr(target, '__name__', target)!r}, which is not a page.",
                       "Pass a function declared with `@ui.page`. For an outside site use `href=\"https://...\"`.")
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


def piece(ctx: Build, name: str, value: Any) -> Any:
    """``value`` as one piece of an address, encoded so that it stays one piece.

    A value from data must not be able to change the page or add a parameter: a fixed value is
    quoted here, a state value by the browser.
    """
    import reflex as rx

    instead = "Pass text or a number: `key=row.id`, `key=7`."
    if isinstance(value, rx.Var):
        kind = typing.get_origin(value._var_type) or value._var_type
        if kind in (bool, list, dict, tuple, set):
            raise ctx.fail(f"the value for `{name}` in a link is a {getattr(kind, '__name__', kind)} from the state.", instead)
        text = value if kind is str else value.to_string(use_json=False)
        return rx.Var(f"encodeURIComponent({text!s})", _var_data=text._get_all_var_data()).to(str)
    if value is None or isinstance(value, bool) or str(value) in ("", ".", ".."):
        raise ctx.fail(f"the value for `{name}` in a link is {value!r}.", instead)
    return quote(str(value), safe="")
