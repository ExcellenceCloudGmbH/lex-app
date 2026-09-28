"""The Reflex app ``lex reflex`` serves.

Built in one place so that the entry module Reflex imports
(``lex/reflex_app.py``) is nothing but the order things must happen in: Django
first, then this.
"""

from __future__ import annotations


def create_app():
    """The project's Reflex app: sign-in, Django connections, and its dashboards.

    * ``rxe.App`` rather than ``rx.App`` -- the auth plugin refuses anything else,
      and it is what puts every page, handler and var behind sign-in.
    * :class:`DjangoConnectionMiddleware`, so each event gets the connection
      handling a Django request gets.
    * ``/``, which shows whichever dashboard the query string asks for.

    Further pages come from the project itself: any ``@rxe.page`` in
    ``_reflex_structure.py``, or in a module it imports, is added when the app
    compiles.
    """
    import reflex_enterprise as rxe

    from lex.lex_app.reflex.dashboards import (
        LexDashboardState,
        dashboard_page,
        load_structure,
    )
    from lex.lex_app.reflex.django_orm import DjangoConnectionMiddleware

    app = rxe.App()
    app.add_middleware(DjangoConnectionMiddleware())
    app.add_page(
        dashboard_page(load_structure()),
        route="/",
        on_load=LexDashboardState.resolve,
    )
    return app
