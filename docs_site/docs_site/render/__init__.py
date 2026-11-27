"""A content page as components, and its "On this page" list."""

import reflex as rx
import rfds

from docs_site.content import Page
from docs_site.render.blocks import LexDocTransformer


_rendered: dict[tuple[str, str], rx.Component] = {}


def render_page(page: Page) -> rx.Component:
    """The page's components, built once per process: a second call runs none of its code again,
    so the states its ```python exec``` blocks define are never redefined."""
    key = (page.slug, page.source)
    if key not in _rendered:
        _rendered[key] = LexDocTransformer(page, live=not page.slug.startswith("reference/")).transform(page.document)
    return _rendered[key]


def toc(page: Page) -> rx.Component:
    """RFDS's anchor nav (scroll-spy marks the heading in view) under a visible "On this page"
    title; ``###`` entries carry a class the frame indents them by."""
    if not page.headings:
        return rx.fragment()
    entries = [(anchor_id, rx.el.span(text, class_name=f"docs-toc-h{level}"))
               for level, text, anchor_id in page.headings]
    return rx.fragment(
        rx.el.p(rfds.icon("text", size="sm"), "On this page", class_name="docs-toc-title",
                custom_attrs={"aria-hidden": "true"}),
        rfds.anchor_nav(*entries, label="On this page", sticky=False, id="docs-toc-nav"),
    )
