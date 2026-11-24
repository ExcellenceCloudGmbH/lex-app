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
    #: The permission (or permissions) a raw page's ``requires=`` gives; a declared page's guard is
    #: what lex-app registered for it (:func:`~lex.lex_app.reflex.ui.pages.frame.page_guard`).
    permission: Any = None
    #: A raw page's rules (``@ui.rule``) in its ``requires=``.
    rules: tuple = ()
    #: A count beside the entry: a function of the user, or a whole-number state value.
    badge: Any = None


_NAV: list[NavEntry] = []


@dataclasses.dataclass(frozen=True)
class Place:
    """Where a page sits, for its breadcrumbs: its area, its title, and the listed page it opens from."""

    area: str
    title: Any                          # a sentence, or a state value (a record's name)
    parent: str | None = None           # the parent page's route
    parent_title: str | None = None
    #: A view inside the page, shown as one more crumb while it is open: (the condition, its words) --
    #: a calculation's run (``?run=``), under its page. ``None``: the page has one view.
    view: Any = None


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
    """RFDS's breadcrumbs for the page at ``route``: its area, its parent, itself; ``None`` without an area.

    While a view inside the page shows (a calculation's run), the page is a link back and the view the last crumb.
    """
    place = place_of(route)
    if place is None:
        return None
    import rfds

    crumbs: list[dict] = [{"label": _crumb_label(place.area)}]          # an area is not a page: text
    if place.parent:
        crumbs.append({"label": _crumb_label(place.parent_title), "href": place.parent})
    page = {"label": _crumb_label(place.title)}
    trail = rfds.page(rfds.breadcrumbs(*crumbs, page))
    if place.view is None:
        return trail
    import reflex as rx

    # A view inside the page (a run): the page becomes the way back to its first view, the view its crumb.
    showing, words = place.view
    back = {**page, "href": route} if "[" not in route else page
    return rx.cond(showing, rfds.page(rfds.breadcrumbs(*crumbs, back, {"label": _crumb_label(words)})), trail)


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
    """The areas in the order the navigation lists them: ``ui.frame(nav=)``'s first, then every other area
    (a raw page's) in the order its first page was declared."""
    from lex.lex_app.reflex.ui.pages import frame

    order = list(frame.current().nav)
    return order + [area for area in dict.fromkeys(entry.section for entry in _NAV) if area not in order]


def check_nav(names: dict[str, str] | None = None) -> None:
    """Refuse a lex.ui page whose area is a typo: listed nowhere, neither by ``ui.frame(nav=)`` nor by a raw page.

    ``names`` gives the page function's name for the route of each lex.ui page. A raw page
    (``ui.reflex_page``, ``lex_page``) is never refused: its area follows the listed ones, in the order
    of its first page, so a project need not order the areas of pages it has not moved yet.
    """
    for entry in _NAV:
        if names is not None and entry.route in names:
            check_area(entry.route, entry.section, names.get(entry.route))


def _raw_areas() -> list[str]:
    """The areas of the raw pages listed in the navigation (pages not declared with ``@ui.page``)."""
    from lex.lex_app.reflex.ui.pages import declare

    return list(dict.fromkeys(entry.section for entry in _NAV if entry.route not in declare._DECLARED))


def check_area(route: str, area: str, name: str | None = None) -> None:
    """Refuse the area ``area`` of the lex.ui page at ``route`` when neither ``ui.frame(nav=)`` nor a raw page has it."""
    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import PageError

    order = frame.current().nav
    raw = _raw_areas()
    if order and area not in order and area not in raw:
        page = f"Page `{name}` ({route})" if name else f"The page at {route}"
        listed = ", ".join(repr(one) for one in order)
        others = [one for one in raw if one not in order]
        also = f" or an area of a raw page ({', '.join(repr(one) for one in others)})" if others else ""
        raise PageError(f"{page} is in nav={area!r}, which ui.frame(nav=[{listed}]) does not list, nor does any "
                        f"raw page.\nWrite one of {listed}{also}, or add {area!r} to ui.frame(nav=).")


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


@dataclasses.dataclass(frozen=True)
class PerUserSection:
    """A section of the navigation whose links a function of the user gives (``ui.section(title, fn)``)."""

    title: str
    key: str


def badge_of(key: str, badge: Any) -> Any:
    """The count an entry shows: the server's answer for a function of the user, a state value as it is."""
    import reflex as rx

    from lex.lex_app.reflex.ui.state import State as UiState

    if badge is None:
        return None
    if isinstance(badge, rx.Var):
        return badge.to(int)
    return UiState.ui_frame_counts.get(key, 0).to(int)


def nav_layout() -> list:
    """The side navigation, in order: the overview, then each area or section of ``ui.frame(nav=)`` --
    RFDS's ``NavSection``\\ s, and :class:`PerUserSection`\\ s for sections a function of the user fills.

    A guarded entry carries its key as RFDS's ``permission``: :func:`lex_chrome` draws it only once the
    server has decided that this user may open it (:func:`_decided_on_the_server`).
    """
    from rfds.components.navigation.side_nav import NavItem, NavSection

    from lex.lex_app.reflex.ui.pages import frame

    configured = frame.current()
    if not _NAV and not configured.sections:
        return []
    guarded = frame.guards()
    layout: list = [NavSection(items=(NavItem("lex.nav.overview", "Overview", "/", icon="dashboard", exact=True),))]
    headings: dict[str, list[NavEntry]] = {area: [] for area in _areas()}
    for entry in _NAV:
        headings[entry.section].append(entry)
    for heading, entries in headings.items():
        section = configured.section(heading)
        if section is not None and section.of_user is not None:
            layout.append(PerUserSection(heading, frame.per_user_key(heading)))
            continue
        items = [
            NavItem(f"lex.nav.{_slug(entry.route)}", entry.label, entry.route, icon=entry.icon,
                    permission=entry.route if entry.route in guarded else None,
                    badge=badge_of(entry.route, entry.badge))
            for entry in entries
        ]
        for index, link in enumerate(section.links if section is not None else ()):
            key = frame.link_key(heading, index)
            items.append(NavItem(f"lex.nav.link.{_slug(heading)}.{index}", link.label, frame.href_of(link)[0],
                                 icon=link.icon, permission=key if key in guarded else None,
                                 badge=badge_of(key, link.badge)))
        if items:
            layout.append(NavSection(items=tuple(items), label_id=f"lex.nav.section.{_slug(heading)}",
                                     label_default=heading))
    return layout


def nav_sections() -> tuple:
    """The side navigation's RFDS sections, in order (:func:`nav_layout` without the per-user sections)."""
    from rfds.components.navigation.side_nav import NavSection

    return tuple(one for one in nav_layout() if isinstance(one, NavSection))


def _current(href: Any) -> Any:
    """RFDS's ``aria-current`` for an entry whose address is a state value (RFDS's own takes text)."""
    import reflex as rx
    from reflex_base.vars.base import VarData

    path = rx.State.router.url.path
    return rx.Var(f"(((p, h) => (p === h || p.startsWith(h + '/')) ? 'page' : undefined)("
                  f"String({path} ?? '').replace(/\\/$/, '') || '/', String({href} ?? '').replace(/\\/$/, '') || '/'))",
                  _var_type=str, _var_data=VarData.merge(path._get_all_var_data(), href._get_all_var_data()))


def _per_user(section: PerUserSection) -> Any:
    """A section of links the server gave this user, drawn as RFDS draws its navigation's sections."""
    import reflex as rx
    import rfds
    from rfds.components._base import with_tooltip

    from lex.lex_app.reflex.ui.pages.parts import say
    from lex.lex_app.reflex.ui.state import State as UiState

    links = UiState.ui_frame_links.get(section.key, []).to(list[dict[str, str]])

    def one(link: Any) -> Any:
        label, href = link["label"].to(str), link["href"].to(str)
        content = (rx.el.span(link["initial"].to(str), class_name="rfds-side-nav-icon rfds-side-nav-initial",
                              custom_attrs={"aria-hidden": "true"}),
                   rx.el.span(label, class_name="rfds-side-nav-label"))
        attrs = {"aria-current": _current(href)}

        def anchor(outside: bool) -> Any:
            return with_tooltip(rfds.link(*content, href=href, external=outside, tone="neutral", underline="auto",
                                          class_name="rfds-side-nav-link", custom_attrs=attrs),
                                label, side="right", class_name="rfds-side-nav-tip")

        return rx.el.li(rx.cond(link["outside"].to(str) == "yes", anchor(True), anchor(False)),
                        class_name="rfds-side-nav-item", custom_attrs={"data-rfds-level": "1"})

    heading = f"lex-nav-section-{_slug(section.title)}"
    return rx.cond(links.length() > 0, rx.el.div(
        rx.el.h2(say(section.title), id=heading, class_name="rfds-side-nav-heading"),
        rx.el.ul(rx.foreach(links, one), class_name="rfds-side-nav-list",
                 custom_attrs={"role": "list", "aria-labelledby": heading}),
        class_name="rfds-side-nav-section"))


def _nav_footer() -> Any:
    """``ui.frame(nav_footer=)`` in RFDS's navigation footer; ``None`` without one."""
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import say
    from lex.lex_app.reflex.ui.pages.vocab import Link
    from lex.lex_app.reflex.ui.state import State as UiState

    part = frame.current().nav_footer
    if part is None:
        return None
    if isinstance(part, Link):
        href, outside = frame.href_of(part)
        content: Any = rfds.link(say(part.label), href=href, external=outside, tone="neutral", underline="auto")
        if "nav_footer" in frame.guards():
            content = rx.cond(UiState.ui_frame_shown.contains("nav_footer"), content)
    else:
        content = rx.text(say(part.sentence, **part.values), size="1")
    return rx.el.div(content, class_name="rfds-side-nav-footer lex-nav-footer")


def _shown_if_decided(key: str, component: Any) -> Any:
    """``component``, drawn only once the server has answered that this user may see the entry ``key``."""
    import reflex as rx

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.state import State as UiState

    if key not in frame.guards():
        return component
    return rx.cond(UiState.ui_frame_shown.contains(key), component)


def frame_value(key: str) -> Any:
    """The words the server answered for the frame's part ``key`` (a function of the user)."""
    from lex.lex_app.reflex.ui.state import State as UiState

    return UiState.ui_frame_values.get(key, "").to(str)


def header_link(key: str, link: Any) -> Any:
    """A ``ui.link`` of the header: its icon, its words and its count, drawn on the server's answer."""
    import reflex as rx
    import rfds
    from rfds.icons import icon

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import say

    href, outside = frame.href_of(link)
    content: list[Any] = []
    if link.icon:
        content.append(icon(link.icon, size="sm"))
    content.append(rx.el.span(say(link.label), class_name="lex-header-label"))
    count = badge_of(key, link.badge)
    if count is not None:
        content.append(rx.cond(count > 0, rfds.count_badge(count, label=say(link.label))))
    drawn = rfds.link(*content, href=href, external=outside, tone="neutral", underline="auto",
                      class_name="lex-header-link")
    return _shown_if_decided(key, drawn)


def header_actions() -> tuple:
    """``ui.frame(actions=)`` as the header band's parts: a status's or a text's words from a function of
    the user shown while it answers something; links on the server's answer; the rest as in a page."""
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import Build, with_overlays
    from lex.lex_app.reflex.ui.pages.vocab import Link, Status, Text

    ctx = Build(page="ui.frame", slot="actions")
    drawn: list[Any] = []
    for index, part in enumerate(frame.current().actions):
        key = frame.link_key("actions", index)
        if isinstance(part, Link):
            drawn.append(header_link(key, part))
            continue
        words = part.word if isinstance(part, Status) else part.sentence if isinstance(part, Text) else None
        if words is not None and frame._plain_function(words):
            value = frame_value(key)
            shown = (rfds.lozenge(value, tone=part.tone) if isinstance(part, Status) else rx.text(value, size="2"))
            drawn.append(part._wrap(ctx, rx.cond(value != "", shown)))
            continue
        drawn.append(part.build(ctx))
    return (with_overlays(ctx, rx.fragment(*drawn)),) if drawn else ()


def brand() -> Any:
    """The header's brand: the project's mark (``ui.frame(logo=)``) in RFDS's logo slot and the product's
    name, one link home (the overview)."""
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.ui.pages import frame

    configured = frame.current()
    parts: list[Any] = []
    if configured.logo:
        # Decorative beside the name, as RFDS says, unless the project names it.
        parts.append(rx.el.span(rx.el.img(src=configured.logo, alt=configured.logo_label),
                                class_name="rfds-header-logo"))
    parts.append(rx.el.span(product_name(), class_name="rfds-header-product"))
    return rfds.link(*parts, href="/", tone="neutral", underline="auto", class_name="lex-header-home")


def frame_banner() -> Any:
    """``ui.frame(banner=)`` as RFDS's banner, shown while its sentence is not empty; ``None`` without one."""
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import Build, say

    message = frame.current().banner
    if message is None:
        return None
    sentence = message.sentence
    if frame._plain_function(sentence):
        words: Any = frame_value("banner")
    else:
        words = say(sentence, **message.values)
    drawn = rfds.banner(tone=message.tone, title=words)
    if isinstance(words, rx.Var):
        drawn = rx.cond(words.to(str) != "", drawn)
    return message._wrap(Build(page="ui.frame", slot="banner"), drawn)


def user_menu_items() -> tuple:
    """``ui.frame(user_menu=)`` as RFDS's user menu entries, each drawn on the server's answer."""
    from rfds.components.actions.menu import menu_item

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import say

    entries: list[Any] = []
    for index, link in enumerate(frame.current().user_menu):
        href, _outside = frame.href_of(link)
        entries.append(_shown_if_decided(frame.link_key("user_menu", index),
                                         menu_item(say(link.label), href=href, icon=link.icon)))
    return tuple(entries)


def header_search() -> Any:
    """RFDS's search box in the header, when ``ui.frame(search=)`` names the project's search page:
    Enter opens it with what was typed (:meth:`State.ui_frame_search`)."""
    import reflex as rx
    import rfds

    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.pages.parts import say
    from lex.lex_app.reflex.ui.state import State as UiState

    if frame.current().search is None:
        return None
    box = rfds.search_input(value=UiState.ui_frame_query, on_change=UiState.ui_frame_type, label=say("Search"),
                            id="lex-frame-search")
    return rx.form(box, on_submit=UiState.ui_frame_search, reset_on_submit=False, class_name="lex-header-search")


def _any_shown(section: Any, drawn: Any) -> Any:
    """A section whose entries are all guarded, drawn only while the server shows one of them: a heading
    without entries would only say that something is there."""
    import functools

    import reflex as rx

    from lex.lex_app.reflex.ui.state import State as UiState

    keys = [item.permission for item in section.items]
    if not keys or any(key is None for key in keys):
        return drawn
    return rx.cond(functools.reduce(lambda one, other: one | other,
                                    [UiState.ui_frame_shown.contains(key) for key in keys]), drawn)


def side_nav() -> Any:
    """RFDS's side navigation of this project: its sections in order, the per-user ones among them, and its foot."""
    import rfds

    with _decided_on_the_server():
        layout = nav_layout()
        if not layout:
            return None
        from rfds.components.navigation.side_nav import NavSection

        drawn = rfds.side_nav(*(one for one in layout if isinstance(one, NavSection)))
    built = iter(list(drawn.children))
    children = [_any_shown(one, next(built)) if isinstance(one, NavSection) else _per_user(one) for one in layout]
    footer = _nav_footer()
    drawn.children = [*children, *built, *([footer] if footer is not None else [])]
    return drawn


class _decided_on_the_server:
    """While the navigation is built, RFDS's entry check is the server's answer for this user.

    lex-app's entries carry their key as RFDS's ``permission`` (:func:`nav_sections`); an entry is drawn
    once ``ui_frame_shown`` -- what the server decided when the page opened -- holds that key. RFDS's own
    checker (the browser's permissions) is put back afterwards, for anything else that asks it.
    """

    def __enter__(self) -> None:
        import importlib

        from lex.lex_app.reflex.ui.state import State as UiState

        side_nav = importlib.import_module("rfds.components.navigation.side_nav")   # the module, not the function

        self._before = list(side_nav._CHECKER)        # read only to put it back: RFDS has no getter
        side_nav.set_permission_checker(lambda key: UiState.ui_frame_shown.contains(key))

    def __exit__(self, *exc: Any) -> None:
        from rfds.components.navigation.side_nav import set_permission_checker

        set_permission_checker(self._before[0] if self._before else None)


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


#: The title of a page that does not exist -- Reflex's, so a refused page's tab says what a missing one's does.
def not_found_title() -> str:
    from reflex_base.constants.route import Page404

    return Page404.TITLE


def not_found_notice() -> Any:
    """What a page that does not exist says: RFDS's empty state, with the way to the overview."""
    import rfds

    from lex.lex_app.reflex.ui.pages.parts import say

    return rfds.empty_state(kind="error", title=say("This page does not exist"),
                            description=say("Check the address, or go to the overview."),
                            primary_action=rfds.link(say("Overview"), href="/"))


def not_found_body(path: Any) -> Any:
    """The body of lex-app's not-found page: the same for a missing page and a refused one
    (``ui.frame(refused_as_missing=True)``)."""
    import rfds

    from lex.lex_app.reflex.ui.pages.parts import say

    return rfds.page(rfds.page_header(title=say("Page not found"), path=path), not_found_notice())


def not_found_page() -> Any:
    """lex-app's not-found page (Reflex's ``404`` route), in lex-app's frame; ``create_app`` adds it."""
    from reflex_base.constants.route import Page404

    return lex_chrome(not_found_body("/" + Page404.SLUG))


def _granted(route: str) -> Any:
    """What the opener answered for the page at ``route``: "yes", "no", or '' until it has."""
    from lex.lex_app.reflex.ui.state import State as UiState

    return UiState.ui_access.get(route, {}).get("granted", "").to(str)


def _decided(route: str, body: Any, *, missing: bool = False) -> Any:
    """``body`` once the opener has asked the page's rules and they granted; their refusal otherwise --
    the no-access notice, or with ``missing`` the not-found page's body.

    Until the opener has answered for this page nothing is shown.
    """
    import reflex as rx

    from lex.lex_app.reflex.ui.state import State as UiState

    granted = _granted(route)
    if missing:
        refused = not_found_body(route)
    else:
        message = UiState.ui_access.get(route, {}).get("message", "").to(str)
        refused = rx.cond(message != "", denied_notice(message=message), denied_notice())
    return rx.cond(granted == "yes", body, rx.cond(granted == "no", refused, rx.fragment()))


def _conceals(needed: tuple, rules: tuple) -> bool:
    """Whether a page with these requirements is shown as missing when refused (``refused_as_missing``)."""
    from lex.lex_app.reflex.ui.pages import frame

    return frame.current().refused_as_missing and bool(needed or rules)


def lex_chrome(body, *, embedded=None, route=None, missing=False):
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
        product=brand(),
        environment=environment(),
        search=header_search(),
        actions=header_actions(),
        # `?is_logout_enabled=false` leaves the user's name without the way out.
        user_menu=rx.cond(
            LexDashboardState.logout_enabled,
            rfds.user_menu(name=LexUser.display_name, email=LexUser.email, items=user_menu_items(),
                           sign_out_href=sign_out_href()),
            rx.text(LexUser.display_name, size="2"),
        ),
    )
    side = side_nav()
    trail = breadcrumbs(route)
    if trail is not None:
        body = rx.fragment(trail, body)
    if missing:
        # A page refused as missing gives nothing away: its breadcrumbs wait for the opener's answer too.
        body = _decided(route, body, missing=True)
    from lex.lex_app.reflex.ui.pages import frame
    from lex.lex_app.reflex.ui.state import State as UiState

    # What the frame shows this user is decided on the server whenever a page opens (on any page in
    # lex-app's frame, a raw one too): the shell's mount asks, in the background.
    deciding = {"on_mount": UiState.ui_frame_open} if frame.deciding() else {}
    return rx.fragment(
        theme_follower(),
        auth_notice("restored"),
        # One calculation-log dialog per page, for every lex_widgets frame on it.
        log_dialog(),
        rfds.app_shell(
            header=header,
            side_nav=side,
            main=body,
            banner=frame_banner(),
            class_name=rx.cond(embedded, EMBEDDED_CLASS, ""),
            **deciding,
        ),
    )


def lex_page(
    route: str,
    *,
    title: str,
    nav: str | None = None,
    icon: str | None = None,
    requires: Any = None,
    on_load: Any = None,
    _rules: tuple = (),
    _badge: Any = None,
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

    needed = (requires,) if isinstance(requires, str) else tuple(requires or ())

    def decorator(page_fn: Callable[[], Any]) -> Callable[[], Any]:
        import reflex as rx

        from lex.lex_app.reflex.access import LexAccessState

        if nav:
            # A page registered again -- a hot reload re-imports its module --
            # keeps its place in the navigation.
            list_in_nav(NavEntry(route=route, label=title, section=nav, icon=icon, badge=_badge,
                                 permission=needed[0] if needed else None))
            set_place(route, Place(nav, title))

        def page():
            body = page_fn()
            if _conceals(needed, _rules):
                return lex_chrome(body, route=route, missing=True)
            if _rules:
                body = _decided(route, body)
            for one in reversed(needed):
                from rfds.auth import can

                body = can(one, body, fallback=denied_notice())
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
        register_page(route, required, optional, needed[0] if len(needed) == 1 else needed, _rules, title)
        loads: list[Any] = [LexAccessState.load, *others, UiState.ui_open(route)]
        # A page refused as missing: its tab says what a missing page's does until the opener has granted.
        shown_title = rx.cond(_granted(route) == "yes", title, not_found_title()) if _conceals(needed, _rules) else title
        rx.page(route=route, title=shown_title, on_load=loads, **page_kwargs)(page)
        return page_fn

    return decorator

