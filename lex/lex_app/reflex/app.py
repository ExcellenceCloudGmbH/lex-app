"""The Reflex app ``lex reflex`` serves.

Built in one place so that the entry module Reflex imports
(``lex/reflex_app.py``) is nothing but the order things must happen in: Django
first, then this.
"""

from __future__ import annotations


def create_app():
    """The project's Reflex app: RFDS, sign-in, Django connections, and its dashboards.

    lex-app builds the app and RFDS installs into it (decision D1), on plain
    Reflex and RFDS's open path -- no Reflex Enterprise, and no Reflex account
    (decision E1):

    * ``rx.App(**rfds.app_kwargs())`` -- RFDS's head (pre-paint script, fonts),
      toaster and error handlers. The page language is ``LEX_UI_LANGUAGE``.
    * RFDS's platform with lex-app's security headers, installed before
      ``rfds.install`` so that dashboards may be framed by lex-app and nobody
      else (decision D10).
    * Sign-in through lex-app's own (decision E2,
      :mod:`lex.lex_app.reflex.session`): the hand-off routes and the
      event-socket guard outermost on the backend, the session middleware
      before every other one, and RFDS's identity resolver.
    * RFDS's ``PolicyMiddleware``: a signed-in user passes a handler without a
      check, and :func:`~lex.lex_app.reflex.auth.lex_event`'s ``auth=`` checks
      run before theirs (decision E6). Then ``rfds.install(app)``, which keeps
      it, and :func:`~lex.lex_app.reflex.access.install_permission_resolver`,
      which maps Keycloak's grants to RFDS's permissions (decision D3).
    * :class:`DjangoConnectionMiddleware`, so each event gets the connection
      handling a Django request gets.
    * :class:`~lex.lex_app.reflex.access.GrantsRefreshMiddleware`, so RFDS's
      permission checks answer from grants at most a minute old.
    * lex-app's single-port proxy, when ``REFLEX_USE_SINGLE_PORT`` asks for it
      (decision E7).
    * ``/``, which shows whichever dashboard the query string asks for,
      ``/ui-error``, where a failed ``@ui.load`` sends the browser, and
      ``/logout``, where RFDS's user menu signs out.

    Further pages come from the project itself: any ``@ui.page``,
    ``@lex_page`` or ``@rx.page`` in ``_reflex_structure.py``, or in a module it
    imports, is added when the app compiles (``@ui.page`` pages are registered
    once that module is loaded, so a page may link to one declared after it). Once they are declared, no page route may have
    a dynamic argument named like a ``ui.State`` helper (``[user]``, ``[state]``...):
    Reflex would make it a field of every state and hide the helper. Nor may it have one named
    like a field of any state (``/regions/[region]`` beside a field ``region``), which Reflex
    refuses only when it builds the frontend.
    """
    import reflex as rx
    import rfds
    from rfds.auth import PolicyMiddleware, permission_var
    from rfds.components.navigation.side_nav import set_permission_checker
    from rfds.platform import install_platform

    from lex.lex_app.reflex import devproxy, session
    from lex.lex_app.reflex.access import (
        GrantsRefreshMiddleware,
        LexAccessState,
        install_permission_resolver,
    )
    from lex.lex_app.reflex.auth import LexUser
    from lex.lex_app.reflex.dashboards import (
        LexDashboardState,
        dashboard_page,
        load_structure,
    )
    from lex.lex_app.reflex.design import (
        format_settings,
        framing_policy_transformer,
        lex_head_components,
        lex_security_headers,
    )
    from lex.lex_app.reflex.django_orm import DjangoConnectionMiddleware

    language = format_settings()["ui_language"]
    app = rx.App(**rfds.app_kwargs(html_lang=language, extra_head=lex_head_components()))
    install_platform(app, headers=lex_security_headers())
    app.api_transformer = [*app.api_transformer, framing_policy_transformer()]
    session.install(app)
    auth = session.proxy_auth()
    app.add_middleware(PolicyMiddleware(auth, default="allow"))
    rfds.install(app, tenant=None, auth=auth)
    install_permission_resolver()
    set_permission_checker(permission_var)

    app.add_middleware(DjangoConnectionMiddleware())
    app.add_middleware(GrantsRefreshMiddleware())
    devproxy.install(app)
    from lex.lex_app.reflex.ui.handlers import register_page
    from lex.lex_app.reflex.ui.pages import declare
    from lex.lex_app.reflex.ui.state import State as UiState

    _load_lazy_states()  # rfds.toast defines states; imported mid-event, they would register too late
    structure = load_structure()
    # The project's module is loaded: its @ui.page pages, /ui-error, the jobs' clock and the route check.
    declare.install(app)
    register_page("/", [], [], "")
    app.add_page(
        dashboard_page(structure),
        route="/",
        on_load=[LexAccessState.load, LexDashboardState.resolve, UiState.ui_open("/")],
    )
    app.add_page(_signing_out, route="/logout", title="Signing out", on_load=LexUser.sign_out)
    from reflex_base.constants.route import Page404

    from lex.lex_app.reflex.shell import not_found_page

    # lex-app's not-found page, in its frame: a page refused as missing (ui.frame(denied_as_missing=True))
    # shows the same. A project that added its own "404" page keeps it.
    if Page404.SLUG not in getattr(app, "_unevaluated_pages", {}):
        app.add_page(not_found_page, route=Page404.SLUG, title=Page404.TITLE, on_load=[LexAccessState.load])
    return app


def _load_lazy_states() -> None:
    """Import the RFDS modules that define Reflex states on first use, before any page is added.

    ``rfds.toast`` is imported lazily (a lex.ui toast reaches for it); first imported in the
    middle of an event, it would register its states after the user's state tree was built.
    """
    import rfds

    rfds.toast  # noqa: B018 - loading it is the point


def _signing_out():
    """What ``/logout`` shows while the browser goes to the backend's sign-out."""
    import rfds

    return rfds.page(rfds.page_header(title="Signing out", path="/logout"), rfds.spinner(label="Signing out"))
