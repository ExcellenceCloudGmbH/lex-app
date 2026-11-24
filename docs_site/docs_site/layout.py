"""The site's frame: header, sidebar, the page, "On this page", previous and next."""

import reflex as rx

from docs_site import nav
from docs_site.content import load
from docs_site.render import render_page, toc


def route_of(slug: str) -> str:
    return "/" if slug == "index" else "/" + slug


def _title(slug: str) -> str:
    return load(slug).title


def _header() -> rx.Component:
    return rx.hstack(
        rx.link(rx.heading("lex-app dashboards", size="4"), href="/", underline="none"),
        rx.spacer(),
        rx.box(id="docs-search-slot"),
        padding="12px 24px", border_bottom="1px solid var(--gray-a5)", width="100%", align="center",
    )


def _sidebar(current: str) -> rx.Component:
    items = []
    for section in nav.NAV:
        items.append(rx.text(section.title, weight="bold", size="2", margin_top="12px"))
        for group in section.groups:
            if len(section.groups) > 1:
                items.append(rx.text(group.title, size="1", color_scheme="gray", margin_top="4px"))
            for slug in group.pages:
                items.append(rx.link(_title(slug), href=route_of(slug), size="2",
                                     weight="medium" if slug == current else "regular",
                                     color_scheme="teal" if slug == current else "gray",
                                     aria_current="page" if slug == current else None))
    return rx.el.nav(
        rx.vstack(*items, spacing="1", align="start"),
        aria_label="Docs", width="240px", flex_shrink="0", padding="16px", class_name="docs-sidebar",
        display=rx.breakpoints(initial="none", md="block"),
    )


def _prev_next(slug: str) -> rx.Component:
    before, after = nav.neighbours(slug)
    return rx.hstack(
        rx.link(f"← {_title(before)}", href=route_of(before)) if before else rx.box(),
        rx.link(f"{_title(after)} →", href=route_of(after)) if after else rx.box(),
        justify="between", width="100%", margin_top="32px",
    )


def shell(slug: str, body: rx.Component, *, aside: rx.Component | None = None) -> rx.Component:
    return rx.vstack(
        _header(),
        rx.hstack(
            _sidebar(slug),
            rx.box(body, flex="1", min_width="0", max_width="760px",
                   padding=rx.breakpoints(initial="16px", md="24px")),
            rx.el.nav(aside, aria_label="On this page", width="200px", flex_shrink="0", padding="24px",
                      display=rx.breakpoints(initial="none", lg="block")) if aside is not None else rx.fragment(),
            align="start", width="100%",
        ),
        spacing="0", width="100%",
    )


def _content_page(slug: str):
    def page() -> rx.Component:
        doc = load(slug)
        section = nav.section_of(slug)
        body = rx.vstack(
            rx.text(section.title, size="2", color_scheme="gray"),
            rx.heading(doc.title, size="8", as_="h1"),
            rx.text(doc.description, size="4", color_scheme="gray") if doc.description else rx.fragment(),
            render_page(doc),
            _prev_next(slug),
            spacing="4", align="stretch",
        )
        return shell(slug, body, aside=toc(doc))
    page.__name__ = page.__qualname__ = "page_" + slug.replace("/", "_").replace("-", "_")
    return page


def _landing() -> rx.Component:
    doc = load("index")
    cards = [rx.link(rx.card(rx.heading(s.title, size="4"), rx.text(s.blurb, size="2")),
                     href=route_of(s.groups[0].pages[0]), underline="none")
             for s in nav.NAV]
    body = rx.vstack(rx.heading(doc.title, size="8", as_="h1"), render_page(doc),
                     rx.grid(*cards, columns=rx.breakpoints(initial="1", sm="2", lg="3"), spacing="4"),
                     spacing="5", align="stretch")
    return shell("index", body)


def add_pages(app: rx.App) -> None:
    app.add_page(_landing, route="/", title=load("index").title, description=load("index").description)
    for slug in nav.ordered():
        doc = load(slug)
        app.add_page(_content_page(slug), route=route_of(slug), title=f"{doc.title} · lex-app dashboards",
                     description=doc.description)
