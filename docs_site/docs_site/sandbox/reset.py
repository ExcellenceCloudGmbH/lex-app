"""The "Reset sample data" button on a demo card: the tab's copy of the sample data starts over."""

import sys
from typing import Any

import reflex as rx
import rfds

from docs_site.sandbox.store import STORE

HELP = ("Puts this tab's sample data back as it started. Tables on the page read it again at once; "
        "other figures on their next action.")


def page_module(path: str) -> str:
    """The module a page's code runs in, from the page's route (``/pages/tables``)."""
    slug = path.strip("/") or "index"
    return "docs_site.pages." + slug.replace("/", "_").replace("-", "_")


def rows_handlers(path: str) -> list:
    """Every ``@ui.rows`` handler the page at ``path`` defines."""
    from lex.lex_app.reflex.ui.rows import is_rows

    module = sys.modules.get(page_module(path))
    found = []
    for value in list(vars(module).values()) if module is not None else []:
        if isinstance(value, type) and issubclass(value, rx.State):
            found.extend(h for h in value.__dict__.values() if is_rows(h))
    return found


class SampleData(rx.State):
    @rx.event
    def reset_sample_data(self) -> list[Any]:
        STORE.reset(str(self.router.session.client_token or ""))
        events: list[Any] = [rfds.toast.success("Sample data reset",
                                                 description="This tab has the sample data as it started.")]
        handlers = rows_handlers(self.router.page.path)
        if handlers:
            from lex import ui

            events.extend(ui.refresh(*handlers))
        return events


def button() -> rx.Component:
    return rfds.tooltip(rfds.button("Reset sample data", intent="tertiary", size="sm", icon="rotate-ccw",
                                    on_click=SampleData.reset_sample_data),
                        content=HELP)
