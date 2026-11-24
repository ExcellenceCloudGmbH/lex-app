"""lex-app's chrome around a Reflex dashboard, and ``@lex_page`` to add pages inside it.

A dashboard opened on its own gets RFDS's application shell: lex-app's header
with the signed-in user and the way out, and a side navigation listing the
project's pages. Opened inside lex-app (``?lex_embed=1``) it gets neither,
because lex-app's own chrome already shows both -- the same contract the
Streamlit dashboards keep. Either way it follows lex-app's colour mode and
tells lex-app's shell when it had to stop for a sign-in.

A project adds pages with :func:`lex_page`, the counterpart of ``@rx.page``::

    from lex.lex_app.reflex import lex_page, permission

    @lex_page("/cash", title="Cash overview", nav="Finance",
              requires=permission(CashPosition, "read"))
    def cash():
        return rfds.page(rfds.page_heading("Cash overview"), ...)

Each page is signed-in only, loads the user's Keycloak grants
(:class:`~lex.lex_app.reflex.access.LexAccessState`) before its own load
events, and, with ``nav=``, appears in the side navigation under that heading.
"""

from __future__ import annotations

import dataclasses
import os
import re
from typing import Any, Callable

#: Where the user signs out by default (``ui.frame(sign_out=)`` changes it): the page ``create_app``
#: adds, which hands over to the backend's ``/auth/logout``.
SIGN_OUT_HREF = "/logout"

def sign_out_href() -> str:
    """Where the user signs out: ``ui.frame(sign_out=)``, else :data:`SIGN_OUT_HREF`."""
    from lex.lex_app.reflex.ui.pages import frame

    return frame.current().sign_out or SIGN_OUT_HREF


#: ``LEX_ENVIRONMENT`` values RFDS's header can badge.
ENVIRONMENTS = ("prod", "uat", "dev", "none")

#: The class the shell carries inside lex-app's frame; ``lex_rfds.css`` hides
#: the header and the navigation under it.
EMBEDDED_CLASS = "lex-embedded"


@dataclasses.dataclass(frozen=True)
class NavEntry:
    """One page in the side navigation."""

    route: str
    label: str
    section: str
    icon: str | None = None
    permission: str | None = None


_NAV: list[NavEntry] = []


@dataclasses.dataclass(frozen=True)
class Place:
    """Where a page sits, for its breadcrumbs: its area, its title, and the listed page it opens from."""

    area: str
    title: Any                          # a sentence, or a state value (a record's name)
    parent: str | None = None           # the parent page's route
    parent_title: str | None = None


#: Route -> where the page sits; a page without an area has no entry, and no breadcrumbs.
_PLACES: dict[str, Place] = {}


def set_place(route: str, place: Place) -> None:
    _PLACES[route] = place


def place_of(route: str | None) -> Place | None:
    """Where the page at ``route`` sits; ``None`` for a page outside every area."""
    return _PLACES.get(route) if route else None


def _crumb_label(text: Any) -> Any:
    """A crumb's words, translated as every sentence is; a state value as it stands."""
    import reflex as rx

    from lex.lex_app.reflex.ui.pages.parts import say

    # RFDS's crumb takes text; a component carries a translated sentence or a state value as well.
    return rx.fragment(say(text))


def breadcrumbs(route: str | None) -> Any:
    """RFDS's breadcrumbs for the page at ``route``: its area, its parent, itself; ``None`` without an area."""
    place = place_of(route)
    if place is None:
        return None
    import rfds

    crumbs: list[dict] = [{"label": _crumb_label(place.area)}]          # an area is not a page: text
    if place.parent:
        crumbs.append({"label": _crumb_label(place.parent_title), "href": place.parent})
    crumbs.append({"label": _crumb_label(place.title)})
    return rfds.page(rfds.breadcrumbs(*crumbs))


def nav_entries() -> list[NavEntry]:
    """The pages registered with ``nav=``, in registration order."""
    return list(_NAV)


def clear_nav() -> None:
    """Forget every registered navigation entry (tests, and recompiles in one process)."""
    _NAV.clear()


def list_in_nav(entry: NavEntry) -> None:
    """List ``entry`` in the navigation; a page listed again (a reload, or its title known later)
    keeps its place."""
    places = [i for i, known in enumerate(_NAV) if known.route == entry.route]
    if places:
        _NAV[places[0]] = entry
    else:
        _NAV.append(entry)


def _areas() -> list[str]:
    """The areas in the order the navigation lists them: ``ui.frame(nav=)``'s, else first declared first."""
    from lex.lex_app.reflex.ui.pages import frame

    order = list(frame.current().nav)
    return order + [area for area in dict.fromkeys(entry.section for entry in _NAV) if area not in order]


def check_nav(names: dict[str, str] | None = None) -> None:
    """Refuse a page listed in an area ``ui.frame(nav=)`` does not list (a typo would make a new area).

    ``names`` gives the page function's name for a route, for the message.
    """
    for entry in _NAV:
        check_area(entry.route, entry.section, (names or {}).get(entry.route))


def check_area(route: str, area: str, name: str | None = None) -> None:
    """Refuse the area ``area`` of the page at ``route`` when ``ui.frame(nav=)`` does not list it."""
    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import PageError

    order = frame.current().nav
    if order and area not in order:
        page = f"Page `{name}` ({route})" if name else f"The page at {route}"
        listed = ", ".join(repr(one) for one in order)
        raise PageError(f"{page} is in nav={area!r}, which ui.frame(nav=[{listed}]) does not list.\n"
                        f"Write one of {listed}, or add {area!r} to ui.frame(nav=).")


def _slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "_", text.lower()).strip("_") or "page"


def product_name() -> str:
    """The name the header shows: ``LEX_DASHBOARD_TITLE``, else the project's."""
    title = (os.getenv("LEX_DASHBOARD_TITLE") or "").strip()
    if title:
        return title
    try:
        from lex.lex_app.settings import repo_name
    except Exception:
        return "LEX"
    return str(repo_name).replace("_", " ").replace("-", " ").strip().title() or "LEX"


def environment() -> str:
    """The environment badge: ``LEX_ENVIRONMENT`` when it names one RFDS knows."""
    value = (os.getenv("LEX_ENVIRONMENT") or "none").strip().lower()
    return value if value in ENVIRONMENTS else "none"


def nav_sections() -> tuple:
    """The side navigation: the overview first, then each area's pages, in ``ui.frame(nav=)``'s order."""
    from rfds.components.navigation.side_nav import NavItem, NavSection

    if not _NAV:
        return ()
    sections = [
        NavSection(
            items=(NavItem("lex.nav.overview", "Overview", "/", icon="dashboard", exact=True),)
        )
    ]
    headings: dict[str, list[NavEntry]] = {area: [] for area in _areas()}
    for entry in _NAV:
        headings[entry.section].append(entry)
    for heading, entries in headings.items():
        if not entries:
            continue
        items = tuple(
            NavItem(
                f"lex.nav.{_slug(entry.route)}",
                entry.label,
                entry.route,
                icon=entry.icon,
                permission=entry.permission,
            )
            for entry in entries
        )
        sections.append(
            NavSection(items=items, label_id=f"lex.nav.section.{_slug(heading)}", label_default=heading)
        )
    return tuple(sections)


def denied_notice(message: Any = None):
    """RFDS's access-denied notice, as this project words it (``ui.frame(denied=, request_access=)``).

    ``message`` is the refusal's own sentence (a rule's, a state value); without it the project's
    ``denied=``, else RFDS's. "Switch account" goes to the sign-out address.
    """
    from rfds.patterns.security import access_denied

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import say

    configured = frame.current()
    if message is None and configured.denied:
        message = say(configured.denied)
    return access_denied(inline=True, message=message, request_access_href=configured.request_access or None,
                         switch_account_href=sign_out_href())


def lex_chrome(body, *, embedded=None, route=None):
    """``body`` in lex-app's chrome: RFDS's shell standalone, the bare page inside lex-app.

    With the page's ``route``, the page is headed by its breadcrumbs (:func:`breadcrumbs`), inside
    lex-app's frame too: there they are the way back up. The shell is always compiled; inside lex-app's frame it carries
    :data:`EMBEDDED_CLASS`, which hides its header and navigation. Compiling the
    page once, rather than once per mode, keeps exactly one main landmark and
    one page heading on it, which RFDS's conformance checks count.
    """
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.auth import LexUser
    from lex.lex_app.reflex.dashboards import LexDashboardState
    from lex.lex_app.reflex.embed import log_dialog
    from lex.lex_app.reflex.host import auth_notice, theme_follower

    if embedded is None:
        embedded = LexDashboardState.embedded
    header = rfds.header(
        product=product_name(),
        environment=environment(),
        # `?is_logout_enabled=false` leaves the user's name without the way out.
        user_menu=rx.cond(
            LexDashboardState.logout_enabled,
            rfds.user_menu(name=LexUser.display_name, email=LexUser.email, sign_out_href=sign_out_href()),
            rx.text(LexUser.display_name, size="2"),
        ),
    )
    sections = nav_sections()
    side = rfds.side_nav(*sections) if sections else None
    trail = breadcrumbs(route)
    if trail is not None:
        body = rx.fragment(trail, body)
    return rx.fragment(
        theme_follower(),
        auth_notice("restored"),
        # One calculation-log dialog per page, for every lex_widgets frame on it.
        log_dialog(),
        rfds.app_shell(
            header=header,
            side_nav=side,
            main=body,
            class_name=rx.cond(embedded, EMBEDDED_CLASS, ""),
        ),
    )


def lex_page(
    route: str,
    *,
    title: str,
    nav: str | None = None,
    icon: str | None = None,
    requires: str | None = None,
    on_load: Any = None,
    **page_kwargs: Any,
) -> Callable[[Callable[[], Any]], Callable[[], Any]]:
    """Add a signed-in page to the dashboards, in lex-app's chrome.

    Args:
        route: The page's path, e.g. ``"/cash"``.
        title: The browser tab's title, and the page's label in the navigation.
        nav: The navigation heading to list the page under; ``None`` keeps it
            out of the navigation (reachable by link only).
        icon: An RFDS icon concept for the navigation entry.
        requires: An RFDS permission string (see
            :func:`~lex.lex_app.reflex.access.permission`). Without it, the page
            shows RFDS's access-denied content and its navigation entry is
            hidden. This is what the user sees, not access control: the data
            the page loads must be checked by the handlers that load it.
        on_load: Load events, run after the user's grants are loaded. A lex.ui load
            (``@ui.load``) is started after them, by the opener.
        **page_kwargs: Anything else ``rx.page`` takes (``meta``, ``image``...).

    Returns:
        The page function, unchanged, so it can still be called or tested.
    """
    if not route.startswith("/"):
        raise ValueError(f"lex_page() routes start with '/', got {route!r}")
    from lex.lex_app.reflex.ui.state import check_route

    check_route(route)              # a route argument named like a ui.State helper would hide it

    def decorator(page_fn: Callable[[], Any]) -> Callable[[], Any]:
        import reflex as rx

        from lex.lex_app.reflex.access import LexAccessState

        if nav:
            # A page registered again -- a hot reload re-imports its module --
            # keeps its place in the navigation.
            list_in_nav(NavEntry(route=route, label=title, section=nav, icon=icon, permission=requires))
            set_place(route, Place(nav, title))

        def page():
            body = page_fn()
            if requires:
                from rfds.auth import can

                body = can(requires, body, fallback=denied_notice())
            return lex_chrome(body, route=route)

        page.__name__ = page_fn.__name__
        page.__qualname__ = page_fn.__qualname__
        page.__doc__ = page_fn.__doc__
        from lex.lex_app.reflex.ui.handlers import page_loads, register_page
        from lex.lex_app.reflex.ui.state import State as UiState

        # lex.ui loads are background handlers, which Reflex starts at once -- before the
        # grants are loaded. So they are started by the opener, the page's last load event,
        # which names only the route; the loads and the permission stay on the server.
        required, optional, others = page_loads(on_load)
        register_page(route, required, optional, requires or "")
        loads: list[Any] = [LexAccessState.load, *others, UiState.ui_open(route)]
        rx.page(route=route, title=title, on_load=loads, **page_kwargs)(page)
        return page_fn

    return decorator

