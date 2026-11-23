"""@ui.page and @ui.dashboard: where a page lives."""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Callable

from lex.lex_app.reflex.ui.pages import kinds, links
from lex.lex_app.reflex.ui.pages.parts import Build, PageError, called


@dataclasses.dataclass
class Declared:
    fn: Callable[[], Any]
    info: links.PageInfo
    nav: str | None
    icon: str | None
    permission: str | None
    done: bool = False


#: Route -> the page declared for it, until :func:`finalize` hands it to lex-app.
_DECLARED: dict[str, Declared] = {}


_PAGES_DIR = os.path.dirname(os.path.abspath(__file__))


def _raised_by_lex(error: BaseException) -> bool:
    """Whether ``error`` comes from a ``ui.*`` component refusing its arguments (its message says what to write)."""
    tb = error.__traceback__
    while tb is not None and tb.tb_next is not None:
        tb = tb.tb_next
    return tb is not None and os.path.dirname(os.path.abspath(tb.tb_frame.f_code.co_filename)) == _PAGES_DIR


def _kind_of(fn: Callable[..., Any], *args: Any) -> kinds.Kind:
    """What the page function returns, checked; an error of its own is given the page's name."""
    try:
        kind = fn(*args)
    except PageError:
        raise
    except (ValueError, TypeError) as wrong:
        if _raised_by_lex(wrong):
            raise PageError(f"Page `{fn.__name__}`: {wrong}") from wrong
        raise _raised(fn, wrong) from wrong
    except Exception as wrong:
        raise _raised(fn, wrong) from wrong
    if kind is None:
        raise PageError(f"Page `{fn.__name__}`: it returned nothing. Did you forget `return`?\n"
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`.")
    if not isinstance(kind, kinds.Kind):
        raise PageError(f"Page `{fn.__name__}`: it returned {type(kind).__name__}.\n"
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`.")
    return kind


def _raised(fn: Callable[..., Any], wrong: Exception) -> PageError:
    return PageError(f"Page `{fn.__name__}`: its function raised {type(wrong).__name__}: {wrong}\n"
                     "A page function only describes the page: it returns `ui.record(...)` or "
                     "`ui.list(...)` and does nothing else.")


def _named(load: Any) -> str:
    """A handler as the author wrote it: `FundState.close`, which is an `@ui.action`."""
    fn = getattr(load, "fn", None)
    if fn is None:
        return called(load)
    name = ".".join(fn.__qualname__.split(".")[-2:])
    kind = getattr(fn, "__ui_kind__", "")
    return f"`{name}`, which is an `@ui.{kind}`" if kind else f"`{name}`, which is not a lex.ui handler"


def _loads(name: str, kind: kinds.Kind) -> tuple[list, list]:
    """The kind's (page loads, part loads), each checked to be a load a page can start."""
    page_loads, part_loads = kind.loads()
    for load in (*page_loads, *part_loads):
        if getattr(getattr(load, "fn", None), "__ui_kind__", "") != "load":
            raise PageError(f"Page `{name}`: `load=` got {_named(load)}.\n"
                            "Pass a handler declared with `@ui.load`, without arguments: `load=FundState.open`. "
                            "It reads the page's values from `self.params`.")
    seen: list = []
    for load in (*page_loads, *part_loads):
        if any(load is other for other in seen):
            raise PageError(f"Page `{name}`: `{load.fn.__qualname__}` is given as a load twice.\n"
                            "A load belongs to the page or to one section.")
        seen.append(load)
    return page_loads, part_loads


def _permission(route: str, requires: Any) -> str | None:
    """The page's permission string; ``None`` when any signed-in user may see it."""
    from lex.lex_app.reflex.access import permission
    from lex.lex_app.reflex.ui import guard

    if requires is None or requires is guard.signed_in:
        return None
    if isinstance(requires, guard.Requirement):
        return permission(requires.target, requires.scope)
    raise TypeError(f"ui.page({route!r}, requires=) takes one permission: ui.read(Model), ui.create(Model), "
                    f"ui.edit(Model), ui.delete(Model) or ui.permission(...); got {requires!r}. "
                    "Leave it out for a page any signed-in user may see.")


def page(route: str, *, nav: str | None = None, icon: str | None = None, requires: Any = None,
         params: Any = ()) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Declare a page: its address, its place in the navigation, who may see it.

    The function returns a page kind and does nothing else: lex-app calls it when it registers
    the pages -- after the whole module is loaded, so it may name pages declared further down --
    and again when the page is built. A page that is declared wrongly stops the app from starting,
    with an error that names it.

    ``requires=`` and ``visible=`` decide what is shown, never what is allowed: a value a page
    shows reaches the browser. What a user may read or change is enforced by each handler's own
    ``requires=`` and by lex-app's API, which acts as the user.
    """
    if not route.startswith("/"):
        raise ValueError(f"ui.page() routes start with '/', got {route!r}")
    if isinstance(params, str):
        raise TypeError(f"ui.page({route!r}, params=) takes a list of names: params=[{params!r}]")
    permission = _permission(route, requires)

    def decorator(fn: Callable[[], Any]) -> Callable[[], Any]:
        known = _DECLARED.get(route)
        if known is not None and (known.fn.__module__, known.fn.__qualname__) != (fn.__module__, fn.__qualname__):
            raise ValueError(f"ui.page({route!r}): `{known.fn.__name__}` already is the page at this route; "
                             f"`{fn.__name__}` needs a route of its own.")
        fn.__ui_page__ = links.info_of(route, fn.__name__, params)
        _DECLARED[route] = Declared(fn, fn.__ui_page__, nav, icon, permission)
        return fn

    return decorator


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
        name = declared.fn.__name__
        kind = _kind_of(declared.fn)
        title = kind.title
        if declared.nav and not isinstance(title, str):
            raise PageError(f"Page `{name}`: a page listed in the navigation needs a fixed title.\n"
                            "Give the kind `title=\"...\"`, or leave `nav=` out.")
        required, optional = _loads(name, kind)
        kind.build(Build(page=name, route=route))      # every slot checked now: a wrong page stops the start

        def body(fn: Callable[[], Any] = declared.fn, route: str = route) -> rx.Component:
            return _kind_of(fn).build(Build(page=fn.__name__, route=route))

        body.__name__ = name
        body.__qualname__ = declared.fn.__qualname__
        lex_page(route, title=title if isinstance(title, str) else name.replace("_", " ").capitalize(),
                 nav=declared.nav, icon=declared.icon, requires=declared.permission)(body)
        handlers.register_page(route, required, optional, declared.permission or "")
        declared.done = True


def dashboard(fn: Callable[..., Any]) -> Callable[..., Any]:
    """Mark a page that is not a route: the project's ``main()``, or a model's dashboard hook.

    On a model the hook is a class method; ``@classmethod`` goes above ``@ui.dashboard``. A
    dashboard has no opener, so its loads start when their part of the page mounts, and each
    clears its state's fields as it starts: the loads of one dashboard each fill a state of their
    own.
    """

    def built(*args: Any) -> Any:
        kind = _kind_of(fn, *args)
        page_loads, part_loads = _loads(fn.__name__, kind)
        owners: dict[str, str] = {}
        for load in (*page_loads, *part_loads):
            state, _, handler = load.fn.__qualname__.rpartition(".")
            short = state.rpartition(".")[2]
            if state in owners:
                raise PageError(f"Page `{fn.__name__}`: `{short}.{owners[state]}` and `{short}.{handler}` both load "
                                f"into `{short}`.\nOn a dashboard each load clears its state when its part of the "
                                "page appears, so one would clear what the other read. Give each load a state of "
                                "its own, or read everything in one load.")
            owners[state] = handler
        return kind.build(Build(page=fn.__name__, mounts=True))

    built.__name__ = fn.__name__
    built.__qualname__ = fn.__qualname__
    built.__doc__ = fn.__doc__
    built.__ui_dashboard__ = fn
    return built
