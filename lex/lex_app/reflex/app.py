"""The Reflex app ``lex reflex`` serves.

Built in one place so that the entry module Reflex imports
(``lex/reflex_app.py``) is nothing but the order things must happen in: Django
first, then this.
"""

from __future__ import annotations


def create_app():
    """The project's Reflex app: RFDS, sign-in, Django connections, and its dashboards.

    lex-app builds the app and RFDS installs into it (decision D1), so no
    project constructs one:

    * ``rfds.enterprise.app()`` -- an ``rxe.App``, which the auth plugin
      requires and which puts every page, handler and var behind sign-in, with
      RFDS's head (pre-paint script, fonts), toaster and error handlers. The
      page language is ``LEX_UI_LANGUAGE``.
    * RFDS's platform with lex-app's security headers, installed before
      ``rfds.install`` so that dashboards may be framed by lex-app and nobody
      else (decision D10).
    * ``rfds.install(app)`` -- no tenant mode and no header-proxy sign-in:
      Keycloak, through the auth plugin, is the only identity (decision D2).
      RFDS's enterprise installer then maps the plugin's user to RFDS's
      identity, and :func:`~lex.lex_app.reflex.access.install_permission_resolver`
      maps Keycloak's grants to RFDS's permissions (decision D3).
    * :class:`DjangoConnectionMiddleware`, so each event gets the connection
      handling a Django request gets.
    * :class:`~lex.lex_app.reflex.auth.SignInExpiryMiddleware`, so no event is
      served on a profile whose access token has expired. Added here, before the
      auth plugin adds its gate after compiling, so it runs first.
    * ``/``, which shows whichever dashboard the query string asks for.

    Further pages come from the project itself: any ``@lex_page`` or
    ``@rxe.page`` in ``_reflex_structure.py``, or in a module it imports, is
    added when the app compiles.
    """
    import rfds
    import rfds.enterprise
    from rfds.auth import permission_var
    from rfds.components.navigation.side_nav import set_permission_checker
    from rfds.platform import install_platform

    from lex.lex_app.reflex.access import LexAccessState, install_permission_resolver
    from lex.lex_app.reflex.auth import SignInExpiryMiddleware
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
    app = rfds.enterprise.app(html_lang=language, extra_head=lex_head_components())
    install_platform(app, headers=lex_security_headers())
    app.api_transformer = [*app.api_transformer, framing_policy_transformer()]
    rfds.install(app, tenant=None, auth=None)
    install_permission_resolver()
    set_permission_checker(permission_var)

    app.add_middleware(DjangoConnectionMiddleware())
    app.add_middleware(SignInExpiryMiddleware())
    app.add_page(
        dashboard_page(load_structure()),
        route="/",
        on_load=[LexAccessState.load, LexDashboardState.resolve],
    )
    return app
