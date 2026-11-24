"""A content page as components, and its "On this page" list."""

import reflex as rx

from docs_site.content import Page
from docs_site.render.blocks import LexDocTransformer


def render_page(page: Page) -> rx.Component:
    return LexDocTransformer(page).transform(page.document)


def toc(page: Page) -> rx.Component:
    if not page.headings:
        return rx.fragment()
    return rx.vstack(
        rx.text("On this page", weight="bold", size="2"),
        *[rx.link(text, href=f"#{anchor_id}", size="2", padding_left="12px" if level == 3 else "0")
          for level, text, anchor_id in page.headings],
        spacing="1", align="start", class_name="docs-toc",
    )
