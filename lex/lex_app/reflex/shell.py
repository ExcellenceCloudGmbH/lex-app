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

#: Where the user signs out: the route Reflex Enterprise's AuthPlugin serves.
SIGN_OUT_HREF = "/logout"

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


def nav_entries() -> list[NavEntry]:
    """The pages registered with ``nav=``, in registration order."""
    return list(_NAV)


def clear_nav() -> None:
    """Forget every registered navigation entry (tests, and recompiles in one process)."""
    _NAV.clear()


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
    """The side navigation: the overview first, then each heading's pages."""
    from rfds.components.navigation.side_nav import NavItem, NavSection

    if not _NAV:
        return ()
    sections = [
        NavSection(
            items=(NavItem("lex.nav.overview", "Overview", "/", icon="dashboard", exact=True),)
        )
    ]
    headings: dict[str, list[NavEntry]] = {}
    for entry in _NAV:
        headings.setdefault(entry.section, []).append(entry)
    for heading, entries in headings.items():
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


def lex_chrome(body, *, embedded=None):
    """``body`` in lex-app's chrome: RFDS's shell standalone, the bare page inside lex-app.

    The shell is always compiled; inside lex-app's frame it carries
    :data:`EMBEDDED_CLASS`, which hides its header and navigation. Compiling the
    page once, rather than once per mode, keeps exactly one main landmark and
    one page heading on it, which RFDS's conformance checks count.
    """
    import reflex as rx
    import rfds
    from reflex_enterprise.auth import User

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
            rfds.user_menu(name=LexUser.display_name, email=User.email, sign_out_href=SIGN_OUT_HREF),
            rx.text(LexUser.display_name, size="2"),
        ),
    )
    sections = nav_sections()
    side = rfds.side_nav(*sections) if sections else None
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
        on_load: Load events, run after the user's grants are loaded.
        **page_kwargs: Anything else ``rxe.page`` takes (``meta``, ``image``...).

    Returns:
        The page function, unchanged, so it can still be called or tested.
    """
    if not route.startswith("/"):
        raise ValueError(f"lex_page() routes start with '/', got {route!r}")

    def decorator(page_fn: Callable[[], Any]) -> Callable[[], Any]:
        import reflex_enterprise as rxe

        from lex.lex_app.reflex.access import LexAccessState

        if nav:
            # A page registered again -- a hot reload re-imports its module --
            # keeps its place in the navigation.
            entry = NavEntry(route=route, label=title, section=nav, icon=icon, permission=requires)
            places = [i for i, known in enumerate(_NAV) if known.route == route]
            if places:
                _NAV[places[0]] = entry
            else:
                _NAV.append(entry)

        def page():
            body = page_fn()
            if requires:
                from rfds.auth import can
                from rfds.patterns.security import access_denied

                body = can(requires, body, fallback=access_denied(inline=True))
            return lex_chrome(body)

        page.__name__ = page_fn.__name__
        page.__qualname__ = page_fn.__qualname__
        page.__doc__ = page_fn.__doc__
        loads: list[Any] = [LexAccessState.load]
        if on_load is not None:
            loads.extend(on_load if isinstance(on_load, (list, tuple)) else [on_load])
        rxe.page(route=route, title=title, on_load=loads, **page_kwargs)(page)
        return page_fn

    return decorator

