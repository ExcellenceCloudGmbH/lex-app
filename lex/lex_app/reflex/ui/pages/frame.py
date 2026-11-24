"""``ui.frame``: how lex-app's frame around every page behaves in one project.

lex-app draws the frame -- the header, the navigation, the breadcrumbs, the no-access notice --
and a project configures it with a few generic parts, once, in its dashboard module beside its
``@ui.page``\\ s. It is not a frame of the project's own: nothing here draws.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import sys
from typing import Any, Mapping

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class Frame:
    """What ``ui.frame(...)`` configured; every part optional."""

    nav: tuple[str, ...] = ()
    sign_out: str = ""                  # '' = lex-app's own /logout
    denied: str = ""                    # '' = RFDS's sentence
    request_access: str = ""            # '' = no "Request access" link
    opened: Any = None                  # the project's opened(user, page), or None
    module: str = ""


#: The project's frame, set by :func:`frame`; ``None`` until then (lex-app's defaults).
_FRAME: Frame | None = None


def current() -> Frame:
    """The project's frame, or lex-app's defaults when the project configured none."""
    return _FRAME or Frame()


@dataclasses.dataclass(frozen=True)
class OpenedPage:
    """The page ``ui.frame(opened=)``'s function is given."""

    route: str                          # as declared: "/orders/[key]"
    address: str                        # as the browser has it, with its values: "/orders/7?tab=lines"
    title: str                          # its title; a state title as the page's load left it
    area: str                           # its area in the navigation ('' outside every area)
    params: Mapping[str, Any]           # the page's route and query values


def frame(*, nav: Any = (), denied: Any = None, request_access: Any = None, sign_out: Any = None,
          opened: Any = None) -> None:
    """Configure lex-app's frame for this project: call it once, in the dashboard module.

    ``nav`` is the navigation's areas in the order they are listed: the same words a page names
    its area with (``@ui.page(..., nav="Orders")``). A page whose area is not in the list stops the
    start. Without it, areas come in the order their first page is declared::

        ui.frame(nav=["Orders", "Reports", "Tools"])

    ``denied`` is the sentence a user reads where a page refuses them (instead of RFDS's), and
    ``request_access`` their way out: a page, or an ``https://`` or ``mailto:`` address, shown as
    RFDS's "Request access" link. The notice's "Switch account" link -- sign in with another
    account -- goes to the sign-out address::

        ui.frame(denied="Ask your administrator for access.", request_access="mailto:it@example.com")

    ``opened`` is a function of the project's that lex-app calls when a page opens, for a "recently
    used" list or an audit entry: ``opened(user, page)``, with the signed-in user and the page
    (``page.route``, ``page.address``, ``page.title``, ``page.area``, ``page.params``). It runs after
    the page's loads have succeeded (at once on a page without them), on a worker thread, and the page
    does not wait for it; its failure is logged, never shown. It is not called for a refused page or
    one whose load failed::

        def remember(user, page):
            RecentPage.objects.update_or_create(user_id=user.id, address=page.address,
                                                defaults={"title": page.title})

    ``sign_out`` is where the user menu's "Sign out" leads: a page (``@ui.page`` or
    ``ui.reflex_page``) or an ``https://`` address. By default it is lex-app's ``/logout``, which
    ends the dashboard's sign-in and lex-app's; a project's address should end there too, or the
    dashboard's sign-in lasts until it expires.

    The product name in the header is ``LEX_DASHBOARD_TITLE`` and its badge ``LEX_ENVIRONMENT``: a
    deployment's values. The look is lex-app's. Called again from the same module (a reload), the
    new configuration replaces the old; from another module it is refused.
    """
    global _FRAME
    module = str(sys._getframe(1).f_globals.get("__name__", ""))
    if _FRAME is not None and _FRAME.module != module:
        raise ValueError(f"ui.frame() is already configured, in module {_FRAME.module!r}. A project configures "
                         f"lex-app's frame once, in its dashboard module; move this call ({module!r}) there.")
    if denied is not None and (not isinstance(denied, str) or not denied.strip()):
        raise TypeError(f"ui.frame(denied=) takes the sentence a refused user reads, such as "
                        f"denied=\"Ask your administrator for access.\"; got {denied!r}.")
    if opened is not None:
        _check_opened(opened)
    _FRAME = Frame(nav=_areas(nav), opened=opened, sign_out="" if sign_out is None else target(sign_out, argument="sign_out"),
                   denied=denied or "",
                   request_access="" if request_access is None else target(request_access, argument="request_access",
                                                                           mail=True),
                   module=module)


def _areas(nav: Any) -> tuple[str, ...]:
    if isinstance(nav, str):
        raise TypeError(f"ui.frame(nav=) takes the areas as a list, in their order: nav=[{nav!r}].".replace("'", '"'))
    try:
        areas = tuple(nav)
    except TypeError:
        raise TypeError(f"ui.frame(nav=) takes the areas as a list of names, such as nav=[\"Orders\", \"Reports\"]; "
                        f"got {nav!r}.") from None
    for area in areas:
        if not isinstance(area, str) or not area.strip():
            raise TypeError(f"ui.frame(nav=) takes area names, the words pages give as nav=; got {area!r}.")
    twice = sorted({area for area in areas if areas.count(area) > 1})
    if twice:
        raise ValueError(f"ui.frame(nav=) lists {twice[0]!r} twice; list each area once.")
    return areas


def _check_opened(fn: Any) -> None:
    names: list = []
    if callable(fn) and not inspect.iscoroutinefunction(fn) and not inspect.isclass(fn):
        try:
            names = [p.name for p in inspect.signature(fn).parameters.values()
                     if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        except (TypeError, ValueError):
            names = []
    if len(names) != 2:
        raise TypeError(f"ui.frame(opened=) takes a plain function of (user, page), such as "
                        f"def remember(user, page): ...; got {fn!r}.")


#: The calls of ``opened`` in flight: kept so that they are not collected before they finish.
_CALLS: set = set()


async def page_opened(state: Any, route: str, address: str, session: Any = None) -> None:
    """Hand the page ``route`` -- now shown -- to the project's ``opened`` function, without waiting for it.

    Called by the opener and by a page's last required load, holding the user's lock: it reads the
    page's title from the state and starts the call on a worker thread. Once a visit: a reconnect of
    the same page (its loads run again) is not a new opening.
    """
    visit = getattr(state, "ui_visit", None)
    if visit is not None:
        if getattr(state, "_ui_opened", 0) == visit:
            return
        state._ui_opened = visit
    hook = current().opened
    if hook is None:
        return
    try:
        from lex.lex_app.reflex.shell import place_of
        from lex.lex_app.reflex.ui import context, handlers, pools

        if session is None:
            session = await context.resolve(state)
        place = place_of(route)
        entry = handlers.page_of(route)
        title = await _title_of(state, entry.title if entry is not None else None)
        page = OpenedPage(route=route, address=address, title=title, area=place.area if place else "",
                          params=dict(session.params._values))
        user = session.user
    except Exception:  # noqa: BLE001 - the page is shown; the project reads the log
        logger.exception("lex.ui: ui.frame(opened=) could not be given page %s", route)
        return

    async def call() -> None:
        try:
            await pools.run("interactive", hook, user, page)
        except Exception:  # noqa: BLE001 - never the user's
            logger.exception("lex.ui: ui.frame(opened=) failed for page %s (%s)", route, address)

    task = asyncio.get_running_loop().create_task(call())
    _CALLS.add(task)
    task.add_done_callback(_CALLS.discard)


async def _title_of(state: Any, title: Any) -> str:
    """A page's title as text: as written, or the value of the state field it names."""
    import reflex as rx

    if isinstance(title, str):
        return title
    if not isinstance(title, rx.Var):
        return ""
    data = title._get_all_var_data()
    if data is None or not data.state or not data.field_name:
        return ""
    owner = await state.get_state(rx.State.get_class_substate(data.state))
    value = getattr(owner, data.field_name.removesuffix("_rx_state_"), "")
    return "" if value is None else str(value)


def target(where: Any, *, argument: str, mail: bool = False) -> str:
    """The address ``where`` leads to: a page (``@ui.page`` or ``ui.reflex_page``) without route
    arguments, or an outside address; refused with what to write."""
    from lex.lex_app.reflex.ui.pages import links

    info = links.page_info(where)
    if info is not None:
        if info.arguments:
            raise ValueError(f"ui.frame({argument}=) leads to page `{info.name}` ({info.route}), which needs "
                             f"{', '.join(info.arguments)}. Name a page without route arguments.")
        return info.route
    schemes = ("https://", "mailto:") if mail else ("https://",)
    if isinstance(where, str) and where.startswith(schemes) and len(where) > len("mailto:"):
        return where
    also = " or a mailto: address" if mail else ""
    raise ValueError(f"ui.frame({argument}=) takes a page -- a function declared with @ui.page, or "
                     f"ui.reflex_page(\"/route\") -- or an outside address starting with https://{also}; got "
                     f"{where!r}. A route is not written as text.")


__all__ = ["Frame", "OpenedPage", "current", "frame", "page_opened", "target"]

