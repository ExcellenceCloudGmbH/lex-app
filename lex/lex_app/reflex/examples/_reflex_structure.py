"""Reference Reflex dashboard -- copy this to ``<your_repo>/_reflex_structure.py``.

HOW IT IS LOADED. ``lex/reflex_app.py`` sets Django up, then does, in effect::

    import <your_repo>._reflex_structure as reflex_structure
    app.add_page(<a page whose default view is reflex_structure.main()>, route="/")

Three consequences worth knowing before anything else:

* ``main()`` is called **once, when the app compiles** -- not per request, and
  not per user. It returns the component tree; everything that varies at run
  time (the data, the user) lives in a state and reaches the page as vars.
* Every page, event handler and state var requires sign-in unless it says
  otherwise. ``auth=False`` opts one out; a check function narrows it further.
* Event handlers run on Reflex's event loop, where Django refuses to run its
  synchronous ORM. Use Django's async ORM (``await Model.objects.acount()``) or
  hand synchronous code to ``run_orm``, as ``_row_counts`` below is.

``main()`` is the only name the framework looks for. Further pages are ordinary
``@rxe.page`` functions -- ``about`` below is one -- and are added when the app
compiles, because this module is imported before it does.
"""

from __future__ import annotations

import reflex as rx
import reflex_enterprise as rxe
from reflex_enterprise.auth import User

from lex.lex_app.reflex import LexUser, current_permissions, run_orm


def _row_counts() -> list[dict]:
    """Every model of the project and how many rows it holds -- plain Django.

    Synchronous on purpose: this is the code a Streamlit dashboard would run
    as-is. ``run_orm`` is what lets a Reflex event handler call it.
    """
    from django.apps import apps

    from lex.lex_app.settings import repo_name

    try:
        models = apps.get_app_config(repo_name).get_models()
    except LookupError:
        return []
    rows = [
        {"model": str(model._meta.verbose_name).title(), "rows": model._default_manager.count()}
        for model in models
    ]
    return sorted(rows, key=lambda row: row["model"])


class OverviewState(rx.State):
    """What the overview shows. Protected, like all state: sent once signed in."""

    counts: list[dict] = []
    resources: list[str] = []

    @rx.event
    async def load(self):
        """Read the counts and the viewer's permissions -- on mount, per viewer."""
        self.counts = await run_orm(_row_counts)
        permissions = await current_permissions(self)
        self.resources = sorted({p.get("rsname", "") for p in permissions} - {""})


def _counts_table() -> rx.Component:
    return rx.table.root(
        rx.table.header(
            rx.table.row(
                rx.table.column_header_cell("Model"),
                rx.table.column_header_cell("Rows"),
            )
        ),
        rx.table.body(
            rx.foreach(
                OverviewState.counts,
                lambda row: rx.table.row(
                    rx.table.cell(row["model"]),
                    rx.table.cell(row["rows"]),
                ),
            )
        ),
        width="100%",
    )


def main() -> rx.Component:
    """The dashboard ``/`` shows when the URL names no model."""
    return rx.vstack(
        rx.heading("Overview", size="7"),
        rx.text("Signed in as ", rx.text.strong(LexUser.display_name), "."),
        _counts_table(),
        rx.heading("Keycloak resources you hold a permission on", size="4"),
        rx.cond(
            OverviewState.resources,
            rx.hstack(rx.foreach(OverviewState.resources, rx.badge), wrap="wrap"),
            rx.text("None yet.", color_scheme="gray"),
        ),
        rx.link("About this dashboard", href="/about"),
        on_mount=OverviewState.load,
        width="100%",
        spacing="4",
    )


@rxe.page(route="/about", title="About")
def about() -> rx.Component:
    """A second page. Signed-in only, like every page that does not opt out."""
    return rx.vstack(
        rx.heading("About", size="7"),
        rx.text(
            "Served by lex reflex, signed in through Keycloak, reading the "
            "project's models through the Django ORM."
        ),
        rx.hstack(
            rx.link("Back to the overview", href="/"),
            rx.button("Sign out", on_click=User.logout, variant="soft"),
            spacing="4",
            align="center",
        ),
        padding="1em",
        spacing="4",
    )
