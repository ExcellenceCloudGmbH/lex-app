"""@ui.page and @ui.dashboard: where a page lives."""

from __future__ import annotations

import dataclasses
import os
from typing import Any, Callable

import reflex as rx

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
    kind: Any = None                    # what the function returned when lex-app registered it


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
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`, `ui.form(...)`.")
    if not isinstance(kind, kinds.Kind):
        raise PageError(f"Page `{fn.__name__}`: it returned {type(kind).__name__}.\n"
                        "Return a page kind: `ui.record(...)`, `ui.list(...)`, `ui.form(...)`.")
    return kind


def _raised(fn: Callable[..., Any], wrong: Exception) -> PageError:
    return PageError(f"Page `{fn.__name__}`: its function raised {type(wrong).__name__}: {wrong}\n"
                     "A page function only describes the page: it returns `ui.record(...)`, "
                     "`ui.list(...)` or `ui.form(...)` and does nothing else.")


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

    ``nav`` names the page's area in the navigation (``nav="Orders"``); the page is listed there, by
    its title, after the pages declared before it. ``ui.frame(nav=[...])`` gives the areas' order.
    A page with route arguments (``/orders/[key]``) is in its area -- its breadcrumbs say so -- but
    not listed: it opens from the page that leads to it. Without ``nav`` a page is reached by links
    only.

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
        if nav and not fn.__ui_page__.arguments:      # a page with route arguments is in its area, not listed
            from lex.lex_app.reflex.shell import NavEntry, list_in_nav

            # Its place in the area is where it is declared, among raw pages too; its title, from the
            # kind, replaces the name when lex-app registers the page.
            list_in_nav(NavEntry(route=route, label=fn.__name__.replace("_", " ").capitalize(), section=nav,
                                 icon=icon, permission=permission))
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
        kind = declared.kind = _kind_of(declared.fn)
        title = kind.title
        listed = declared.nav if not declared.info.arguments else None
        if listed and not isinstance(title, str):
            raise PageError(f"Page `{name}`: a page listed in the navigation needs a fixed title.\n"
                            "Give the kind `title=\"...\"`, or leave `nav=` out.")
        required, optional = _loads(name, kind)
        kind.build(Build(page=name, route=route))      # every slot checked now: a wrong page stops the start

        def body(fn: Callable[[], Any] = declared.fn, route: str = route) -> rx.Component:
            return _kind_of(fn).build(Build(page=fn.__name__, route=route))

        body.__name__ = name
        body.__qualname__ = declared.fn.__qualname__
        lex_page(route, title=title if isinstance(title, str) else name.replace("_", " ").capitalize(),
                 nav=listed, icon=declared.icon, requires=declared.permission)(body)
        handlers.register_page(route, required, optional, declared.permission or "")
        declared.done = True
    from lex.lex_app.reflex.shell import check_area, check_nav

    check_nav({route: declared.fn.__name__ for route, declared in _DECLARED.items()})
    for route, declared in _DECLARED.items():
        if declared.nav and declared.info.arguments:
            check_area(route, declared.nav, declared.fn.__name__)
    _place_pages()


def leads_to(kind: Any) -> tuple[list, list]:
    """The pages ``kind`` leads to: (those a table opens, those a button or link leads to)."""
    from lex.lex_app.reflex.ui.pages import tables, vocab

    opens: list = []
    led: list = []
    seen: set[int] = set()

    def walk(part: Any) -> None:
        if isinstance(part, (list, tuple)):
            for one in part:
                walk(one)
            return
        if (not dataclasses.is_dataclass(part) or isinstance(part, (type, rx.Var, rx.Component))
                or id(part) in seen):
            return
        seen.add(id(part))
        if isinstance(part, tables.Table) and links.page_info(part.open) is not None:
            opens.append(part.open)
        if isinstance(part, (vocab.Button, vocab.Link)) and links.page_info(part.to) is not None:
            led.append(part.to)
        for field in dataclasses.fields(part):
            walk(getattr(part, field.name))

    walk(kind)
    return opens, led


def _place_pages() -> None:
    """Where each declared page sits, for its breadcrumbs: a listed page in its area; any other page
    under the first listed page whose table opens it, else whose button or link leads to it."""
    from lex.lex_app.reflex.shell import Place, set_place

    listed = [d for d in _DECLARED.values() if d.nav and not d.info.arguments and d.kind is not None]
    led = {id(d): leads_to(d.kind) for d in listed}
    for route, declared in _DECLARED.items():
        if declared.kind is None:
            continue
        title = declared.kind.title
        if any(d is declared for d in listed):
            set_place(route, Place(declared.nav, title))
            continue
        parent = (next((d for d in listed if any(t is declared.fn for t in led[id(d)][0])), None)
                  or next((d for d in listed if any(t is declared.fn for t in led[id(d)][1])), None))
        area = declared.nav or (parent.nav if parent is not None else None)
        if area:
            set_place(route, Place(area, title, parent.info.route if parent else None,
                                   parent.kind.title if parent else None))


def install(app: Any) -> None:
    """lex.ui's part of lex-app's app (``create_app``): the declared ``@ui.page`` pages, the page a
    failed load goes to (``/ui-error``), the clock that ends abandoned jobs (once), and the route check.

    Called by ``create_app`` once the project's module is loaded; a project does not call it.
    """
    import rfds

    from lex.lex_app.reflex.access import LexAccessState
    from lex.lex_app.reflex.ui import runs
    from lex.lex_app.reflex.ui.state import ERROR_ROUTE, check_app_routes, error_page

    rfds.toast  # noqa: B018 - defines states on first use; imported mid-event they would register too late
    if not getattr(app, "_lex_ui_installed", False):
        app.register_lifespan_task(runs.heartbeat)
        app._lex_ui_installed = True
    finalize()                                     # the project's @ui.page pages
    app.add_page(error_page, route=ERROR_ROUTE, title="This page could not be opened",
                 on_load=[LexAccessState.load])
    check_app_routes(app)


def embed(part: Any, *, name: str = "embedded") -> Any:
    """One lex.ui part -- a ``ui.table``, a ``ui.section`` -- as a Reflex component, for a page
    that is not (yet) written with ``@ui.page``.

    For moving a project one table at a time: ``ui.embed(ui.table(State.rows, columns=[...]))``
    sits in a page built with raw Reflex and RFDS. Its loads and its rows start when it appears;
    ``name`` names it in an error and is a table's caption. A whole page kind goes on a
    ``@ui.page`` or a ``@ui.dashboard``.
    """
    from lex.lex_app.reflex.ui.pages.parts import Part

    if isinstance(part, kinds.Kind):
        raise TypeError("ui.embed() takes one part such as ui.table(...) or ui.section(...); a whole page kind "
                        "goes on a @ui.page or a @ui.dashboard.")
    if not isinstance(part, Part):
        raise TypeError(f"ui.embed() takes a lex.ui part such as ui.table(...); got {called(part)}")
    caption = "" if name == "embedded" else str(name)       # a table's caption: the name it is given
    from lex.lex_app.reflex.ui.pages.parts import with_overlays

    ctx = Build(page=str(name), mounts=True, caption=caption)
    return with_overlays(ctx, part.build(ctx))


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
