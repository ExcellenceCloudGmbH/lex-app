"""``ui.frame``: how lex-app's frame around every page behaves in one project.

lex-app draws the frame -- the header, the navigation, the breadcrumbs, the no-access notice --
and a project configures it with a few generic parts, once, in its dashboard module beside its
``@ui.page``\\ s. It is not a frame of the project's own: nothing here draws.
"""

from __future__ import annotations

import asyncio
import dataclasses
import functools
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
    #: The areas of ``nav=`` with links after their pages, or a function of the user that gives its links.
    sections: tuple["NavLinks", ...] = ()
    nav_footer: Any = None              # a ui.link or ui.text at the foot of the navigation
    search: tuple[str, str] | None = None   # the search page's route and the parameter it reads
    actions: tuple = ()                 # parts in the header band, before the user menu
    user_menu: tuple = ()               # ui.links in the user menu, above "Sign out"
    banner: Any = None                  # a ui.message above every page
    denied_as_missing: bool = False    # a refused page looks like a page that does not exist
    logo: str = ""                      # the address of the project's mark ('' = none)
    logo_label: str = ""                # what the mark says, read out ('' = decorative beside the name)
    module: str = ""

    def section(self, title: str) -> "NavLinks | None":
        return next((one for one in self.sections if one.title == title), None)


@dataclasses.dataclass(frozen=True)
class NavLinks:
    """An area of ``nav=`` with more than its pages: its links, or the function of the user that gives them."""

    title: str
    links: tuple = ()                   # ui.link parts, with fixed values
    of_user: Any = None                 # fn(user) -> the user's links


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


def frame(*, nav: Any = None, nav_footer: Any = None, logo: Any = None, logo_label: Any = None,
          search: Any = None, actions: Any = (), user_menu: Any = (), sign_out: Any = None, denied: Any = None,
          request_access: Any = None, denied_as_missing: bool = False, banner: Any = None, opened: Any = None,
          refused_as_missing: Any = None) -> None:
    """Configure lex-app's frame for this project: call it once, in the dashboard module.

    Every part is optional. **One rule for every value the frame shows** (a count, a word, a sentence, a
    section's links): it is fixed, or a function of the user, ``def waiting(user): ...``, which lex-app
    calls on the server when a page opens, on a worker thread (it may query the database); the page does
    not wait for it, and a failure is logged and leaves its part empty. Who may see an entry (its
    ``requires=``, the page it leads to) is decided there too; nothing else is drawn.

    ==================  ===============================================================================
    **Navigation**
    ``nav``             the areas, in their order, as a dict: each area's name -> ``[]`` (its pages),
                        a list of ``ui.link``\\ s (shown after its pages), or a function of the user
                        that returns the user's links (a section of its own, named like no area that
                        has pages). Areas it does not name follow, in the order their first page is
                        declared; a ``@ui.page`` whose area neither names is a typo and stops the start
    ``nav_footer``      one ``ui.link`` or ``ui.text`` at the foot of the navigation
    **Header**
    ``logo``            the project's mark (``"/acme.svg"`` from the app's assets, or ``https://``)
                        beside the product's name, both leading home; ``logo_label`` names it when it
                        says something the name does not
    ``search``          a page taking one query parameter (``params=["q"]``): RFDS's search box in the
                        header opens it with what was typed, on Enter
    ``actions``         the parts at the top right: ``ui.link`` (``icon=``, ``badge=``, ``requires=``),
                        ``ui.button``, ``ui.status`` and ``ui.custom_block(..., reason=)``
    ``user_menu``       ``ui.link``\\ s in the user menu, above "Sign out"
    ``sign_out``        where "Sign out" leads: a page or ``https://``; lex-app's ``/logout`` by default
    **Denied pages**
    ``denied``          the sentence a refused user reads, instead of RFDS's
    ``request_access``  their way out: a page, ``https://`` or ``mailto:``
    ``denied_as_missing``  ``True``: a refused page looks like lex-app's not-found page
    **Other**
    ``banner``          one ``ui.message`` above every page, shown while its sentence is not empty
    ``opened``          a function lex-app calls when a page has opened: ``opened(user, page)``
    ==================  ===============================================================================

    ::

        def waiting(user):
            return Request.objects.filter(approver=user.id, state="open").count()

        def favourites(user):
            return [ui.link(f.title, to=report, key=f.report_id)
                    for f in Favourite.objects.filter(user=user.id)]

        ui.frame(nav={"Orders": [ui.link("Search orders", to=search, icon="search")],
                      "Reports": [],
                      "Requests": [ui.link("Waiting for you", to=approvals, badge=waiting)],
                      "Favourites": favourites},
                 nav_footer=ui.link("Version 2.4.1", href="https://example.com/changes"),
                 search=search,
                 actions=[ui.link("Notifications", to=inbox, icon="notification", badge=unread)],
                 user_menu=[ui.link("Account", to=account),
                            ui.link("Settings", to=settings, requires=ui.edit(Setting))],
                 denied="Ask your administrator for access.", request_access="mailto:it@example.com",
                 banner=ui.message(config_problem, tone="warning"))

    A link in the frame -- to a page, a raw page, a record or an ``https://`` address -- takes fixed values;
    ``badge=`` (a count beside it) is a function of the user, as on ``@ui.page(nav=, badge=)``, and
    ``requires=`` draws it only for users who meet it. A section of the user's own links shows them as
    the function returns them (no ``icon=``, ``badge=`` or ``requires=``; each leads to a page the user may
    open, or it is left out).

    ``opened(user, page)`` is for a "recently used" list or an audit entry: ``page.route``, ``page.title``
    (a state title as the page's load left it), ``page.area``; ``page.address`` and ``page.params`` are
    the browser's (what it asked for, never a decision). It runs after the page's loads have succeeded (at
    once on a page without them), on a worker thread, and the page does not wait for it; its failure is
    logged, never shown. It is not called for a refused page or one whose load failed.

    ``denied_as_missing=True`` is for applications that must not reveal which pages exist: a page the user
    may not open -- by a permission or a rule -- shows lex-app's not-found page, with the browser tab's
    title, the breadcrumbs and its navigation entry gone too, decided by the page's opener on the server.
    (The page's code is in the app's bundle like every page's: this hides it from the screen, not from
    someone reading the bundle.) ``denied`` and ``request_access`` word a notice it never shows, so they are
    refused beside it. A project's own ``sign_out`` page should end lex-app's sign-in too (``/logout``),
    or the dashboard's sign-in lasts until it expires.

    The product name in the header is ``LEX_DASHBOARD_TITLE`` and its badge ``LEX_ENVIRONMENT``: a
    deployment's values; the look is lex-app's. Called again from the same module (a reload), the new
    configuration replaces the old; from another module it is refused.
    """
    if refused_as_missing is not None:
        raise TypeError("ui.frame(refused_as_missing=) is now denied_as_missing=, the same word as denied= and "
                        f"request_access=: write denied_as_missing={refused_as_missing!r}.")
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
    if not isinstance(denied_as_missing, bool):
        raise TypeError(f"ui.frame(denied_as_missing=) takes True or False; got {denied_as_missing!r}. "
                        "denied_as_missing=True shows a page the user may not open as a page that does not exist.")
    if denied_as_missing and (denied is not None or request_access is not None):
        given = "denied=" if denied is not None else "request_access="
        raise ValueError(f"ui.frame(denied_as_missing=True) shows a refused page as a missing one, so it never shows "
                         f"the no-access notice that {given} words. Leave {given} out, or denied_as_missing out.")
    _check_logo(logo, logo_label)
    areas, sections = _areas(nav)
    _FRAME = Frame(nav=areas, sections=sections, nav_footer=_footer(nav_footer), opened=opened,
                   search=None if search is None else _search(search), actions=_actions(actions),
                   user_menu=_menu(user_menu), banner=_banner(banner), denied_as_missing=denied_as_missing,
                   logo=logo or "", logo_label=logo_label or "", sign_out="" if sign_out is None else target(sign_out, argument="sign_out"),
                   denied=denied or "",
                   request_access="" if request_access is None else target(request_access, argument="request_access",
                                                                           mail=True),
                   module=module)


def _areas(nav: Any) -> tuple[tuple[str, ...], tuple[NavLinks, ...]]:
    """``nav=``: the areas' names in their order, and the areas that have links or a function of the user."""
    from lex.lex_app.reflex.ui.pages.kinds import Section
    from lex.lex_app.reflex.ui.pages.vocab import Link

    if nav is None:
        return (), ()
    if not isinstance(nav, Mapping):
        items = list(nav) if isinstance(nav, (list, tuple)) else [nav]
        names = [one.title if isinstance(one, Section) else one for one in items]
        example = "{" + ", ".join(f"{str(one)!r}: []" for one in names if isinstance(one, str)) + "}"
        if example == "{}":
            example = '{"Orders": [], "Reports": []}'
        also = (" A ui.section is a titled part of a page; an area's links are its list: "
                '{"Help": [ui.link(...)]}.' if any(isinstance(one, Section) for one in items) else "")
        raise TypeError(f"ui.frame(nav=) takes the areas as a dict, in their order -- each area's name -> [] for its "
                        f"pages, a list of ui.link(...) shown after them, or a function of the user for the user's "
                        f"own links: nav={example}.{also}".replace("'", '"'))
    areas: list[str] = []
    sections: list[NavLinks] = []
    for title, content in nav.items():
        if not isinstance(title, str) or not title.strip():
            raise TypeError(f"ui.frame(nav=) names each area with fixed words, the words pages give as nav=; got "
                            f"{title!r}. A count goes on a link: ui.link(..., badge=waiting).")
        areas.append(title)
        if _plain_function(content):
            sections.append(NavLinks(title, of_user=of_user(content, f"the area {title!r} of nav=")))
            continue
        if not isinstance(content, (list, tuple)):
            what = "ui.section" if isinstance(content, Section) else _named(content)
            raise TypeError(f"ui.frame(nav=): the area {title!r} takes [] (its pages), a list of ui.link(...) shown "
                            f"after them, or a function of the user that returns the user's links; got {what}: "
                            f"write {{{title!r}: [ui.link(...)]}}.")
        for one in content:
            if not isinstance(one, Link):
                raise TypeError(f"ui.frame(nav=): the area {title!r} holds ui.link(...)s; got {_named(one)}.")
            entry_link(one, f"the area {title!r} of nav=")
        if content:
            sections.append(NavLinks(title, links=tuple(content)))
    from lex.lex_app.reflex import shell

    for entry in shell.nav_entries():
        check_per_user(entry.section, entry.route, sections)
    return tuple(areas), tuple(sections)


def check_per_user(area: str, route: str, sections: Any = None) -> None:
    """Refuse a page listed in an area that ``nav=`` makes a section of the user's own links: the page would
    be lost from the navigation, or the user's links mixed into an area's."""
    found = next((one for one in (current().sections if sections is None else sections)
                  if one.title == area and one.of_user is not None), None)
    if found is None:
        return
    raise ValueError(f"The page at {route} is in nav={area!r}, and ui.frame(nav=) makes {area!r} a section of the "
                     f"user's own links ({_name(found.of_user)}). An area of pages and a user's links are two "
                     f"sections: give one of them another name, e.g. {area + ' (mine)'!r} for the user's links.")


def _check_logo(logo: Any, label: Any) -> None:
    if logo is not None and not (isinstance(logo, str) and (logo.startswith("https://") or (
            logo.startswith("/") and not logo.startswith("//"))) and len(logo) > 1):
        raise ValueError(f"ui.frame(logo=) takes the address of the mark: from the app's assets, \"/acme.svg\", or "
                         f"an https:// address; got {logo!r}.")
    if label is not None:
        if logo is None:
            raise ValueError("ui.frame(logo_label=) names the mark, and there is none: give its address too, "
                             "logo=\"/acme.svg\".")
        if not isinstance(label, str) or not label.strip():
            raise TypeError(f"ui.frame(logo_label=) takes what the mark says, read out to a screen reader, such as "
                            f"logo_label=\"Acme\"; got {label!r}. Leave it out for a mark beside the product's name.")


def _search(page: Any) -> tuple[str, str]:
    """The search page's route and the one query parameter the box fills."""
    from lex.lex_app.reflex.ui.pages import links

    info = links.page_info(page)
    if info is None:
        raise TypeError(f"ui.frame(search=) takes the search page -- a function declared with @ui.page, or "
                        f"ui.reflex_page(\"/route\", params=[\"q\"]); got {page!r}. A route is not written as text.")
    if info.arguments:
        raise ValueError(f"ui.frame(search=): page `{info.name}` ({info.route}) needs route values; the search page "
                         "takes what was typed as its one query parameter, and no route arguments.")
    if len(info.params) != 1:
        raise ValueError(f"ui.frame(search=): page `{info.name}` ({info.route}) takes {len(info.params)} query "
                         f"parameters; the search page takes exactly one, what was typed: declare it with "
                         f"params=[\"q\"] and read self.params.get(\"q\").")
    return info.route, info.params[0]


#: What the header's actions slot takes: the parts at the top right.
_ACTION_PARTS = "ui.link, ui.button, ui.status or ui.custom_block(..., reason=...)"


def _actions(actions: Any) -> tuple:
    from lex.lex_app.reflex.ui.pages.vocab import Button, CustomBlock, Link, Status, Text

    items = (actions,) if not isinstance(actions, (list, tuple)) else tuple(actions)
    for index, part in enumerate(items):
        if isinstance(part, Link):
            entry_link(part, "actions=")
        elif isinstance(part, Status):
            fixed_or_of_user(part.word, "actions=, the status's word")
        elif isinstance(part, Text):
            raise TypeError("ui.frame(actions=) holds the parts at the top right -- links, buttons, statuses -- and no "
                            "free text: a status says a state in a word, ui.status(sync_state); anything else is a "
                            "ui.custom_block(..., reason=...).")
        elif not isinstance(part, (Button, CustomBlock)):
            raise TypeError(f"ui.frame(actions=) takes {_ACTION_PARTS}; got {_named(part)}.")
    return items


def _menu(items: Any) -> tuple:
    from lex.lex_app.reflex.ui.pages.vocab import Link

    links = (items,) if not isinstance(items, (list, tuple)) else tuple(items)
    for link in links:
        if not isinstance(link, Link):
            raise TypeError(f"ui.frame(user_menu=) takes ui.link(...)s -- the user's own pages: "
                            f"ui.link(\"Account\", to=account); got {_named(link)}.")
        if link.badge is not None:
            raise TypeError(f"ui.frame(user_menu=): the entry {link.label!r} has badge=; a menu entry has no count. "
                            "Put the count beside a link in the header (actions=) or the navigation.")
        entry_link(link, "user_menu=")
    return links


def _banner(message: Any) -> Any:
    from lex.lex_app.reflex.ui.pages.vocab import Message

    if message is None:
        return None
    if not isinstance(message, Message):
        many = " one: RFDS shows one banner at a time" if isinstance(message, (list, tuple)) else ""
        raise TypeError(f"ui.frame(banner=) takes{many or ' one'} ui.message, such as "
                        f"banner=ui.message(config_problem, tone=\"warning\"); got {_named(message)}.")
    fixed_or_of_user(message.sentence, "banner=, the message's sentence")
    return message


def _footer(part: Any) -> Any:
    from lex.lex_app.reflex.ui.pages.vocab import Link, Text

    if part is None:
        return None
    if isinstance(part, Link):
        entry_link(part, "nav_footer=")
        return part
    if isinstance(part, Text):
        return part
    raise TypeError(f"ui.frame(nav_footer=) takes one ui.link or ui.text, such as "
                    f"nav_footer=ui.link(\"Version 2.4.1\", href=\"https://example.com/changes\"); got {_named(part)}.")


def _named(value: Any) -> str:
    from lex.lex_app.reflex.ui.pages.parts import called

    return called(value)


def _plain_function(value: Any) -> bool:
    """Whether ``value`` is a function the project wrote (not a state value, an event, a part or a class)."""
    return inspect.isfunction(value) or inspect.ismethod(value) or (
        isinstance(value, functools.partial))


def fixed_or_of_user(value: Any, where: str) -> None:
    """Refuse a frame value that is neither fixed nor a function of the user (a state value: no page loads a
    state for the frame, so it would show whatever the last page left there)."""
    import reflex as rx

    if isinstance(value, rx.Var):
        raise TypeError(f"ui.frame(): {where} is a state value; a value the frame shows is fixed, or a function of "
                        f"the user that lex-app calls on the server when a page opens: def waiting(user): return ...")
    if _plain_function(value):
        of_user(value, where)


def of_user(fn: Any, where: str) -> Any:
    """``fn``, checked to be a plain function of the user: ``def waiting(user): ...``."""
    names: list = []
    if _plain_function(fn) and not inspect.iscoroutinefunction(fn):
        try:
            names = [p.name for p in inspect.signature(fn).parameters.values()
                     if p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD)]
        except (TypeError, ValueError):
            names = []
    if len(names) != 1:
        raise TypeError(f"ui.frame(): {where} takes a function of the user, which lex-app calls on the server when a "
                        f"page opens: def waiting(user): return ...; got {fn!r}.")
    return fn


def entry_link(link: Any, where: str) -> None:
    """Refuse a frame link lex-app could not draw: a state value in it, a badge that is not a count."""
    import reflex as rx

    if not isinstance(link.label, str) or not link.label:
        raise TypeError(f"ui.frame(): a link in {where} needs its words, a fixed sentence; got {link.label!r}.")
    moving = [name for name, value in link.values.items() if isinstance(value, rx.Var)]
    if moving or isinstance(link.record and link.record[1], rx.Var):
        raise ValueError(f"ui.frame(): the link {link.label!r} in {where} takes fixed values; the frame is the same "
                         f"on every page. For links that depend on the user, give the section a function of the user.")
    if link.to is not None:
        href_of(link)                                   # a page it can address, with its values
    if link.badge is not None:
        fixed_or_of_user(link.badge, f"the badge= of the link {link.label!r}")
        if not _plain_function(link.badge):
            raise TypeError(f"ui.frame(): the badge= of the link {link.label!r} is a count, a function of the user: "
                            f"def waiting(user): return ...; got {link.badge!r}.")


def href_of(link: Any) -> tuple[str, bool]:
    """Where a frame link leads, built in Python, and whether that is an outside site."""
    from lex.lex_app.reflex.ui.pages import links

    if link.to is not None:
        if links.page_info(link.to) is None:
            raise ValueError(f"ui.link({link.label!r}, to=) takes a page -- a function declared with @ui.page, or "
                             f"ui.reflex_page(\"/route\"); got {link.to!r}.")
        where = links.address(link.to, **link.values)
        if not where:
            raise ValueError(f"ui.link({link.label!r}): a value of the page's address is empty.")
        return str(where), False
    if link.record is not None:
        from urllib.parse import quote

        from lex.lex_app.reflex.embed import lex_app_base_url

        model, pk = link.record
        return f"{lex_app_base_url().rstrip('/')}/{model._meta.model_name}/{quote(str(pk), safe='')}/show", True
    return str(link.href), True


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
        user = await context.with_permissions(session)
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


@dataclasses.dataclass(frozen=True)
class Guard:
    """Who may see one entry of the frame: the permission strings it needs, the project's rules, and the
    page values the rules are asked with (none for a listed page; a link's own)."""

    permissions: tuple[str, ...] = ()
    rules: tuple = ()
    params: tuple = ()                  # (name, value) pairs

    def __bool__(self) -> bool:
        return bool(self.permissions or self.rules)


def _strings(requires: Any) -> tuple[str, ...]:
    items = (requires,) if isinstance(requires, str) else tuple(requires or ())
    return tuple(one for one in items if one)


def page_guard(route: str, permission: Any = None, rules: tuple = ()) -> Guard:
    """The guard of the page at ``route``: what lex-app registered for the page (``@ui.page``,
    ``lex_page``), else what its entry says (a raw page's ``visible=``)."""
    from lex.lex_app.reflex.ui import handlers

    page = handlers.page_of(route)
    if page is not None:
        return Guard(_strings(page.requires), tuple(page.rules))
    return Guard(_strings(permission), tuple(rules))


def target_guard(route: str) -> Guard:
    """The guard of the page a link leads to: a declared page's, else a listed raw page's, else none."""
    from lex.lex_app.reflex import shell

    entry = next((one for one in shell.nav_entries() if one.route == route), None)
    return page_guard(route, entry.permission if entry else None, entry.rules if entry else ())


def link_guard(link: Any) -> Guard:
    """Who a frame link is drawn for: its own ``requires=``, and the page it leads to's."""
    from lex.lex_app.reflex.access import permission
    from lex.lex_app.reflex.ui import guard
    from lex.lex_app.reflex.ui.pages import links

    items = tuple(link.requires or ())
    strings = tuple(permission(one.target, one.scope) for one in items if isinstance(one, guard.Requirement))
    rules = guard.rules_of(items)
    info = links.page_info(link.to) if link.to is not None else None
    if info is not None:
        target = target_guard(info.route)
        strings, rules = strings + target.permissions, tuple(dict.fromkeys(rules + target.rules))
    params = tuple(sorted((name, str(value)) for name, value in link.values.items()))
    return Guard(tuple(dict.fromkeys(strings)), rules, params)


@dataclasses.dataclass(frozen=True)
class Entry:
    """One entry of the frame the server decides for: who it is drawn for, and its count."""

    key: str
    guard: Guard = Guard()
    badge: Any = None                   # a function of the user, a state value, or None
    value: Any = None                   # a function of the user giving a word or a sentence


def entries() -> list[Entry]:
    """Every entry of the frame, by its key: a listed page's key is its route, a link's its place."""
    from lex.lex_app.reflex import shell

    configured = current()
    found = [Entry(entry.route, page_guard(entry.route, entry.permission, entry.rules), entry.badge)
             for entry in shell.nav_entries()]
    for section in configured.sections:
        found += [Entry(link_key(section.title, index), link_guard(link), link.badge)
                  for index, link in enumerate(section.links)]
    if configured.nav_footer is not None and hasattr(configured.nav_footer, "requires"):
        found.append(Entry("nav_footer", link_guard(configured.nav_footer), configured.nav_footer.badge))
    found += _placed("actions", configured.actions)
    found += _placed("user_menu", configured.user_menu)
    if configured.search is not None:            # the search box, for users who may open the search page
        found.append(Entry("search", target_guard(configured.search[0])))
    if configured.banner is not None and _plain_function(configured.banner.sentence):
        found.append(Entry("banner", value=configured.banner.sentence))
    return found


def _placed(place: str, parts: tuple) -> list[Entry]:
    """The entries of a list of frame parts: a link's guard and count, a status's or a text's words."""
    from lex.lex_app.reflex.ui.pages.vocab import Button, Link, Status, Text

    found: list[Entry] = []
    for index, part in enumerate(parts):
        key = link_key(place, index)
        if isinstance(part, Link):
            found.append(Entry(key, link_guard(part), part.badge))
        elif isinstance(part, Button):
            found.append(Entry(key, button_guard(part)))
        elif isinstance(part, (Status, Text)):
            words = part.word if isinstance(part, Status) else part.sentence
            if _plain_function(words):
                found.append(Entry(key, value=words))
    return found


def button_guard(button: Any) -> Guard:
    """Who a header button is drawn for: its handler's ``requires=``, as a link is drawn by its own."""
    from lex.lex_app.reflex.access import permission
    from lex.lex_app.reflex.ui import guard

    fn = getattr(getattr(button, "handler", None), "fn", None)
    items = tuple((getattr(fn, "__ui_options__", None) or {}).get("requires") or ())
    strings = tuple(permission(one.target, one.scope) for one in items if isinstance(one, guard.Requirement))
    return Guard(tuple(dict.fromkeys(strings)), guard.rules_of(items))


def link_key(place: str, index: int) -> str:
    """The key of the link ``index`` of a section (or another place) of the frame."""
    return f"{place}#{index}"


def per_user_key(title: str) -> str:
    """The key of a per-user section's links."""
    return f"{title}#"


def guards() -> dict[str, Guard]:
    """Every guarded entry of the frame, by its key."""
    return {entry.key: entry.guard for entry in entries() if entry.guard}


def deciding() -> bool:
    """Whether the frame has anything the server decides when a page opens."""
    return any(entry.guard or _plain_function(entry.badge) or entry.value is not None for entry in entries()) or any(
        section.of_user is not None for section in current().sections)


@dataclasses.dataclass
class Answers:
    """What the server decided for one user when a page opened; only this reaches the browser."""

    user: str = ""                      # the id of the user it was decided for (never sent)
    shown: list[str] = dataclasses.field(default_factory=list)
    counts: dict[str, int] = dataclasses.field(default_factory=dict)
    links: dict[str, list[dict[str, str]]] = dataclasses.field(default_factory=dict)
    values: dict[str, str] = dataclasses.field(default_factory=dict)


async def decide(state: Any) -> Answers:
    """The frame for the user signed in on ``state``'s tab, decided on the server.

    An entry is shown when the user holds its permissions -- Keycloak's grants, fetched with the user's
    token, not what the browser was told -- and every rule grants (on a worker thread, once per rule and
    values in this event). A rule that fails hides its entry, and is logged. The frame's functions of the
    user run on worker threads, each once per event however many entries name it; one that fails, or
    answers what its part cannot show, leaves its part empty, and is logged.
    """
    from lex.lex_app.reflex.access import permission_strings
    from lex.lex_app.reflex.ui import context, handlers, pools

    session = await context.resolve(state)
    held: frozenset[str] | None = None
    asked: dict = {}
    reading: list = []                  # the user with permissions, read once when a rule or function needs it

    def user() -> Any:
        if not reading:
            reading.append(asyncio.ensure_future(context.with_permissions(session)))
        return reading[0]

    async def _rule(rule: Any, params: tuple) -> Any:
        return await pools.run("interactive", handlers._ask, rule, await user(), context.Params(dict(params)))

    async def _function(fn: Any) -> Any:
        return await pools.run("interactive", fn, await user())

    def ask(rule: Any, params: tuple) -> Any:
        """A rule's answer for these values, asked once in this event."""
        if (rule, params) not in asked:
            asked[(rule, params)] = asyncio.ensure_future(_rule(rule, params))
        return asked[(rule, params)]

    def of(fn: Any) -> Any:
        """A function of the user's answer, called once in this event."""
        if (fn, None) not in asked:
            asked[(fn, None)] = asyncio.ensure_future(_function(fn))
        return asked[(fn, None)]

    async def may(key: str, guard: Guard) -> bool:
        nonlocal held
        if guard.permissions:
            if held is None:
                held = permission_strings(await session.permissions())
            if any(one not in held for one in guard.permissions):
                return False
        for rule in guard.rules:
            try:
                answer = await ask(rule, guard.params)
            except Exception:  # noqa: BLE001 - hidden, and logged
                logger.exception("lex.ui: %r failed for the frame's entry %s; the entry is not shown", rule, key)
                return False
            if answer is not True:
                return False
        return True

    async def call(fn: Any, key: str) -> Any:
        try:
            return await of(fn)
        except Exception:  # noqa: BLE001 - the part shows nothing; the project reads the log
            logger.exception("lex.ui: the frame's function %s failed for %s", _name(fn), key)
            return None

    found = entries()
    guarded = [entry for entry in found if entry.guard]
    counted = [entry for entry in found if _plain_function(entry.badge)]
    worded = [entry for entry in found if entry.value is not None]
    sections = [section for section in current().sections if section.of_user is not None]
    results = await asyncio.gather(*(may(e.key, e.guard) for e in guarded), *(call(e.badge, e.key) for e in counted),
                                   *(call(e.value, e.key) for e in worded),
                                   *(call(one.of_user, one.title) for one in sections))
    granted = results[:len(guarded)]
    counts = results[len(guarded):len(guarded) + len(counted)]
    words = results[len(guarded) + len(counted):len(guarded) + len(counted) + len(worded)]
    given = results[len(guarded) + len(counted) + len(worded):]
    answers = Answers(user=session.user.id, shown=[entry.key for entry, yes in zip(guarded, granted) if yes])
    for entry, count in zip(counted, counts):
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            answers.counts[entry.key] = count
        elif count is not None:
            logger.error("lex.ui: the frame's function %s answered %r for %s; a badge is a whole number, 0 or more",
                         _name(entry.badge), count, entry.key)
    for entry, said in zip(worded, words):
        if isinstance(said, str) and said.strip():
            answers.values[entry.key] = said
        elif said is not None and said != "":
            logger.error("lex.ui: the frame's function %s answered %r for %s; it returns the words to show, or None",
                         _name(entry.value), said, entry.key)
    for section, links in zip(sections, given):
        shown = _user_links(section, links)
        # Each leads to a page the user may open: its page's guard, asked with the link's values.
        allowed = await asyncio.gather(*(may(per_user_key(section.title), guard) for _, guard in shown))
        answers.links[per_user_key(section.title)] = [link for (link, _), yes in zip(shown, allowed) if yes]
    if reading and not reading[0].done():
        reading[0].cancel()
    return answers


def _name(fn: Any) -> str:
    return getattr(fn, "__qualname__", repr(fn))


def _user_links(section: NavLinks, links: Any) -> list[tuple[dict[str, str], Guard]]:
    """A per-user section's links as the browser shows them, each with the guard of the page it leads to;
    a link lex-app cannot show is left out, logged."""
    from lex.lex_app.reflex.ui.pages.vocab import Link

    if links is None:
        return []
    shown: list = []
    for link in links if isinstance(links, (list, tuple)) else [links]:
        try:
            if not isinstance(link, Link):
                raise TypeError(f"it returned {link!r}; a per-user section's function returns ui.link(...)s")
            extra = [name for name in ("icon", "badge", "requires") if getattr(link, name) is not None]
            if extra:
                raise TypeError(f"the link {link.label!r} has {'=, '.join(extra)}=; a per-user section's links are "
                                "shown as they are (the function decides who sees what)")
            href, outside = href_of(link)
            label = str(link.label)
        except Exception:  # noqa: BLE001 - left out, and logged
            logger.exception("lex.ui: a link of the frame's section %r is left out", section.title)
            continue
        shown.append(({"label": label, "href": href, "outside": "yes" if outside else "",
                       "initial": label[:1].upper()}, link_guard(link)))
    return shown


__all__ = ["Answers", "Entry", "Frame", "Guard", "NavLinks", "OpenedPage", "current", "decide", "entries", "frame",
           "guards", "page_opened", "target"]

