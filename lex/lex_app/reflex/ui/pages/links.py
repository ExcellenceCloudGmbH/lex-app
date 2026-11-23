"""Where things lead: a link names a page function or a record, never a route string."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable

from lex.lex_app.reflex.ui.pages.parts import Build

_ARGUMENT = re.compile(r"\[([A-Za-z_][A-Za-z0-9_]*)\]")


@dataclasses.dataclass(frozen=True)
class PageInfo:
    """What ``@ui.page`` knows about a page: enough to link to it."""

    name: str
    route: str
    arguments: tuple[str, ...]          # the names in the route's brackets
    params: tuple[str, ...] = ()        # further query parameters the page accepts


def info_of(route: str, name: str, params: Any = ()) -> PageInfo:
    return PageInfo(name, route, tuple(_ARGUMENT.findall(route)), tuple(params))


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
        raise ctx.fail(f"a link to page `{info.name}` ({info.route}) {problem}.",
                       f"Page `{info.name}` takes: {accepted}.")
    path: Any = ""
    rest = info.route
    for name in info.arguments:
        before, _, rest = rest.partition(f"[{name}]")
        path = path + before + as_text(values[name])
    path = path + rest
    query = [(name, values[name]) for name in info.params if name in values]
    for index, (name, value) in enumerate(query):
        path = path + ("?" if index == 0 else "&") + name + "=" + as_text(value)
    return path


def row_href(ctx: Build, target: Callable[..., Any], row_key: str) -> str:
    """The row-link template of a table whose rows open ``target``: its one argument is the row's key."""
    info = page_info(target)
    if info is None or len(info.arguments) != 1:
        raise ctx.fail(f"`open=` needs a page with exactly one route argument, got {getattr(target, '__name__', target)!r}.",
                       "Declare the page as `@ui.page(\"/things/[key]\")`.")
    return info.route.replace(f"[{info.arguments[0]}]", "{" + row_key + "}")


def as_text(value: Any) -> Any:
    """``value`` as a piece of an address: text, or a Var that gives text."""
    import reflex as rx

    return value.to_string(use_json=False) if isinstance(value, rx.Var) and value._var_type is not str else (
        value if isinstance(value, rx.Var) else str(value))
