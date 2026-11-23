"""@ui.page and @ui.dashboard: where a page lives."""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

from lex.lex_app.reflex.ui.pages import kinds, links
from lex.lex_app.reflex.ui.pages.parts import Build, PageError


@dataclasses.dataclass
class Declared:
    fn: Callable[[], Any]
    info: links.PageInfo
    nav: str | None
    icon: str | None
    requires: Any
    done: bool = False


#: Route -> the page declared for it, until :func:`finalize` hands it to lex-app.
_DECLARED: dict[str, Declared] = {}


def _kind_of(fn: Callable[[], Any]) -> kinds.Kind:
    kind = fn()
    if not isinstance(kind, kinds.Kind):
        raise PageError(f"Page `{fn.__name__}`: it returned {type(kind).__name__}.\n"
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`.")
    return kind


def page(route: str, *, nav: str | None = None, icon: str | None = None, requires: Any = None,
         params: Any = ()) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Declare a page: its address, its place in the navigation, who may see it.

    The function returns a page kind. It is called when lex-app builds the pages, after the
    whole module is loaded, so it may name pages declared further down.
    """
    if not route.startswith("/"):
        raise ValueError(f"ui.page() routes start with '/', got {route!r}")

    def decorator(fn: Callable[[], Any]) -> Callable[[], Any]:
        fn.__ui_page__ = links.info_of(route, fn.__name__, params)
        _DECLARED[route] = Declared(fn, fn.__ui_page__, nav, icon, requires)
        return fn

    return decorator


def _permission(requires: Any) -> str | None:
    if requires is None:
        return None
    from lex.lex_app.reflex.access import permission
    from lex.lex_app.reflex.ui import guard

    if isinstance(requires, guard.Requirement):
        return permission(requires.target, requires.scope)
    raise TypeError("ui.page(requires=) takes one of ui.read(Model), ui.create(Model), ui.edit(Model), "
                    f"ui.delete(Model) or ui.permission(...); got {requires!r}")


def finalize() -> None:
    """Hand every declared page to lex-app: chrome, navigation, permission and its loads.

    Called once the project's module is loaded, before the app is compiled.
    """
    import reflex as rx

    from lex.lex_app.reflex.shell import lex_page
    from lex.lex_app.reflex.ui import handlers

    for route, declared in list(_DECLARED.items()):
        if declared.done:
            continue
        kind = _kind_of(declared.fn)
        title = kind.title
        if declared.nav and not isinstance(title, str):
            raise PageError(f"Page `{declared.fn.__name__}`: a page listed in the navigation needs a fixed title.\n"
                            "Give the kind `title=\"...\"`, or leave `nav=` out.")

        def body(fn: Callable[[], Any] = declared.fn, route: str = route) -> rx.Component:
            return _kind_of(fn).build(Build(page=fn.__name__, route=route))

        body.__name__ = declared.fn.__name__
        body.__qualname__ = declared.fn.__qualname__
        permission = _permission(declared.requires)
        lex_page(route, title=title if isinstance(title, str) else declared.fn.__name__.replace("_", " ").capitalize(),
                 nav=declared.nav, icon=declared.icon, requires=permission)(body)
        required, optional = kind.loads()
        handlers.register_page(route, required, optional, permission or "")
        declared.done = True


def dashboard(fn: Callable[[], Any]) -> Callable[[], Any]:
    """Mark a page that is not a route: the project's ``main()``, a model's record dashboard."""

    def built() -> Any:
        return _kind_of(fn).build(Build(page=fn.__name__, mounts=True))

    built.__name__ = fn.__name__
    built.__qualname__ = fn.__qualname__
    built.__doc__ = fn.__doc__
    built.__ui_dashboard__ = fn
    return built
