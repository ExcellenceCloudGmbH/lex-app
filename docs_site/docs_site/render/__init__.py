"""A content page as components, and its "On this page" list."""

import reflex as rx

from docs_site.content import Page
from docs_site.render.blocks import LexDocTransformer


_rendered: dict[tuple[str, str], rx.Component] = {}


def render_page(page: Page) -> rx.Component:
    """The page's components, built once per process: a second call runs none of its code again,
    so the states its ```python exec``` blocks define are never redefined."""
    key = (page.slug, page.source)
    if key not in _rendered:
        _rendered[key] = LexDocTransformer(page).transform(page.document)
    return _rendered[key]


def toc(page: Page) -> rx.Component:
    if not page.headings:
        return rx.fragment()
    return rx.vstack(
        rx.text("On this page", weight="bold", size="2"),
        *[rx.link(text, href=f"#{anchor_id}", size="2", padding_left="12px" if level == 3 else "0")
          for level, text, anchor_id in page.headings],
        spacing="1", align="start", class_name="docs-toc",
    )
