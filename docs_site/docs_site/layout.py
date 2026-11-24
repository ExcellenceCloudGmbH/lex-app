"""The site's frame: header, sidebar, the page, "On this page", previous and next.

The frame is a centred grid under RFDS's header: the sidebar (from 1024 px; below that a drawer
behind the header's menu button), the article, and "On this page" (from 1280 px). Colours are
Radix/RFDS tokens only, so the dark colour mode follows.
"""

import reflex as rx
import rfds

from docs_site import llms, nav
from docs_site.content import load
from docs_site.render import render_page, toc

#: the header's height (RFDS's shell token): sticky columns sit right below it
HEADER = "var(--rfds-size-shell-header, 48px)"
SIDEBAR_FROM = "1024px"     # the sidebar is a column from here, a drawer below
TOC_FROM = "1280px"         # "On this page" is a column from here, hidden below


def route_of(slug: str) -> str:
    return "/" if slug == "index" else "/" + slug


def _title(slug: str) -> str:
    return load(slug).title


# -- header -------------------------------------------------------------------------------------
def _mark() -> rx.Component:
    return rx.box(rfds.icon("layout_dashboard", size="sm"), class_name="docs-mark",
                  custom_attrs={"aria-hidden": "true"})


def _header() -> rx.Component:
    from docs_site import search

    product = rx.link(_mark(), rx.el.span("lex-app", class_name="docs-product-name"),
                      rx.el.span("dashboards", class_name="docs-product-sub"),
                      href="/", underline="none", class_name="docs-product")
    return rfds.header(product=product, actions=[search.box(), mode_toggle()], job_tray=False)


def mode_toggle() -> rx.Component:
    """Light / dark: Reflex's colour mode (stored in the browser, so it holds across pages); the
    Radix theme and RFDS's tokens follow the ``light``/``dark`` class it sets."""
    def button(icon: str, label: str) -> rx.Component:
        return rfds.icon_button(icon, label=label, tooltip=False, on_click=rx.toggle_color_mode,
                                class_name="docs-mode-toggle")

    return rx.color_mode_cond(light=button("moon", "Switch to dark mode"),
                              dark=button("sun", "Switch to light mode"))


# -- sidebar ------------------------------------------------------------------------------------
_GUIDE = tuple(s for s in nav.NAV if s.slug != "reference")
_REFERENCE = tuple(s for s in nav.NAV if s.slug == "reference")


def _nav_link(slug: str, current: str) -> rx.Component:
    here = slug == current
    return rx.el.li(rx.link(_title(slug), href=route_of(slug), underline="none",
                            class_name="docs-nav-link", aria_current="page" if here else None))


def _nav_section(section: nav.Section, current: str) -> rx.Component:
    """One section as a group that opens and closes; the current page's section starts open."""
    holds_current = any(current in g.pages for g in section.groups)
    body = []
    for group in section.groups:
        if len(section.groups) > 1:
            body.append(rx.el.li(group.title, class_name="docs-nav-group", custom_attrs={"role": "presentation"}))
        body += [_nav_link(slug, current) for slug in group.pages]
    return rx.el.details(
        rx.el.summary(rx.el.span(section.title), rfds.icon("chevron_right", size="sm", class_name="docs-nav-chevron"),
                      class_name="docs-nav-summary"),
        rx.el.ul(*body, class_name="docs-nav-list"),
        open=holds_current, class_name="docs-nav-section",
    )


def _sidebar(current: str) -> rx.Component:
    def block(heading: str, sections: tuple[nav.Section, ...]) -> rx.Component:
        return rx.box(rx.el.p(heading, class_name="docs-nav-heading"),
                      *[_nav_section(s, current) for s in sections], class_name="docs-nav-block")

    home = rx.link(rfds.icon("house", size="sm"), "Overview", href="/", underline="none",
                   class_name="docs-nav-link docs-nav-home", aria_current="page" if current == "index" else None)
    # id and data-rfds-shell are what RFDS's header menu button opens below 1024 px (drawer)
    return rx.el.nav(home, block("Guide", _GUIDE), block("Reference", _REFERENCE),
                     aria_label="Docs", id="rfds-side-nav", class_name="docs-sidebar",
                     custom_attrs={"data-rfds-shell": "sidebar"})


# -- article parts ------------------------------------------------------------------------------
def _page_actions(slug: str) -> rx.Component:
    markdown = f"{rx.config.get_config().api_url}/md/{slug}.md"
    return rfds.button_group(
        rfds.button("Copy page", icon="copy", size="sm", on_click=rx.set_clipboard(llms.page_markdown(slug))),
        rx.button(rx.link(rfds.icon("file_text", size="sm"), "View Markdown", href=markdown, is_external=True,
                          underline="none", color_scheme="gray"),
                  as_child=True, variant="soft", color_scheme="gray", size="1", class_name="docs-md-link"),
        label="This page as Markdown", attached=True, class_name="docs-page-actions",
    )


def _prev_next(slug: str) -> rx.Component:
    before, after = nav.neighbours(slug)

    def card(target: str | None, label: str, end: bool) -> rx.Component:
        if target is None:
            return rx.box(class_name="docs-pager-empty")
        return rx.link(rx.el.span(label, class_name="docs-pager-label"),
                       rx.el.span(_title(target), class_name="docs-pager-title"),
                       href=route_of(target), underline="none",
                       class_name="docs-pager-card" + (" docs-pager-next" if end else ""))

    return rx.el.nav(card(before, "Previous", False), card(after, "Next", True),
                     aria_label="Previous and next page", class_name="docs-pager")


# -- the frame ----------------------------------------------------------------------------------
_STYLE = {
    "--docs-sidebar-w": "264px",
    "--docs-toc-w": "220px",
    # a light header (reflex.dev-like) through RFDS's chrome tokens and lex-app's header tokens:
    # the band takes the page's colour (translucent, blurred), text and overlays the page's grays
    "--rfds-color-chrome-bg": "var(--color-background)",
    "--rfds-color-chrome-fg": "var(--gray-12)",
    "--rfds-color-chrome-hover": "var(--gray-a3)",
    "--rfds-color-chrome-selected": "var(--gray-a4)",
    "--rfds-color-chrome-border": "var(--gray-a6)",
    "--rfds-color-chrome-stripe": "var(--gray-a5)",
    "--rfds-color-focus-ring-inverse": "var(--rfds-color-focus-ring, var(--accent-8))",
    # lex-app's theme paints the band through these two (html:root:root .rfds-header)
    "--lex-header-bg": "color-mix(in srgb, var(--color-background) 82%, transparent)",
    "--lex-header-shadow": "inset 0 -1px 0 var(--gray-a5)",
    "&.rfds-shell": {"background_color": "var(--color-background)"},
    # header: the product name and the mark
    "& .docs-product": {"display": "inline-flex", "align_items": "center", "gap": "8px", "color": "inherit",
                        "white_space": "nowrap"},
    "& .docs-mark": {"display": "inline-flex", "align_items": "center", "justify_content": "center",
                     "width": "26px", "height": "26px", "border_radius": "var(--radius-2)",
                     "background": "var(--accent-9)", "color": "var(--accent-contrast)"},
    "& .docs-product-name": {"font_weight": "700", "font_size": "var(--font-size-3)", "letter_spacing": "-0.01em"},
    "& .docs-product-sub": {"display": "none", "font_weight": "400", "font_size": "var(--font-size-3)", "opacity": "0.8"},
    # the header's content lines up with the centred grid below it
    "& .rfds-header": {"padding_inline": "max(12px, calc((100% - 1400px) / 2 + 16px))",
                       "backdrop_filter": "saturate(1.4) blur(10px)"},
    "& .rfds-header-actions": {"margin_inline_start": "auto", "gap": "8px"},
    # the grid
    "& .docs-grid": {"display": "grid", "grid_template_columns": "minmax(0, 1fr)", "width": "100%",
                     "max_width": "1400px", "margin": "0 auto", "flex": "1 1 auto"},
    "& .docs-main": {"min_width": "0", "padding": "24px 16px 64px"},
    "& .docs-article": {"max_width": "760px", "margin": "0 auto", "width": "100%"},
    # sidebar (a drawer below 1024 px)
    "& .docs-sidebar": {"box_sizing": "border-box", "position": "fixed", "top": HEADER, "bottom": "0", "left": "0",
                        "width": "min(300px, 85vw)", "overflow_y": "auto", "padding": "16px 12px 32px",
                        "background": "var(--color-panel-solid)", "box_shadow": "var(--shadow-5)",
                        "visibility": "hidden", "transform": "translateX(-100%)", "z_index": "40",
                        "transition": "transform 160ms ease, visibility 160ms"},
    "&[data-rfds-nav-open='true'] .docs-sidebar": {"visibility": "visible", "transform": "none"},
    "& .rfds-header-menu-slot": {"display": "flex"},
    "& .docs-nav-home": {"margin_bottom": "8px"},
    "& .docs-nav-block + .docs-nav-block": {"margin_top": "20px"},
    "& .docs-nav-heading": {"margin": "0 0 6px", "padding": "0 10px", "font_size": "var(--font-size-1)",
                            "font_weight": "600", "letter_spacing": "0.06em", "text_transform": "uppercase",
                            "color": "var(--gray-10)"},
    "& .docs-nav-summary": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                            "gap": "8px", "padding": "6px 10px", "border_radius": "var(--radius-2)",
                            "cursor": "pointer", "list_style": "none", "font_size": "var(--font-size-2)",
                            "font_weight": "500", "color": "var(--gray-12)", "user_select": "none"},
    "& .docs-nav-summary::-webkit-details-marker": {"display": "none"},
    "& .docs-nav-summary:hover": {"background": "var(--gray-a3)"},
    "& .docs-nav-chevron": {"color": "var(--gray-9)", "transition": "transform 120ms ease", "flex": "none"},
    "& .docs-nav-section[open] > .docs-nav-summary .docs-nav-chevron": {"transform": "rotate(90deg)"},
    "& .docs-nav-list": {"list_style": "none", "margin": "2px 0 8px 18px", "padding": "0 0 0 8px",
                         "border_left": "1px solid var(--gray-a5)", "display": "flex", "flex_direction": "column",
                         "gap": "1px"},
    "& .docs-nav-group": {"padding": "8px 10px 2px", "font_size": "var(--font-size-1)", "font_weight": "500",
                          "color": "var(--gray-10)"},
    "& .docs-nav-link": {"display": "flex", "align_items": "center", "gap": "8px", "padding": "5px 10px",
                         "border_radius": "var(--radius-2)", "font_size": "var(--font-size-2)",
                         "line_height": "20px", "color": "var(--gray-11)", "text_decoration": "none"},
    "& .docs-nav-link:hover": {"background": "var(--gray-a3)", "color": "var(--gray-12)"},
    "& .docs-nav-link[aria-current='page']": {"background": "var(--accent-a3)", "color": "var(--accent-11)",
                                              "font_weight": "500"},
    # "On this page"
    "& .docs-toc": {"display": "none"},
    "& .docs-toc-title": {"margin": "0 0 8px", "padding_left": "12px", "font_size": "var(--font-size-1)",
                          "font_weight": "600", "letter_spacing": "0.06em", "text_transform": "uppercase",
                          "color": "var(--gray-10)"},
    "& .docs-toc .rfds-anchor-nav-link:has(.docs-toc-h3)": {"padding_left": "24px"},
    # article header
    "& .docs-article-head": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                             "flex_wrap": "wrap", "gap": "8px 16px", "margin_bottom": "12px"},
    "& .docs-page-actions .rt-Button": {"gap": "6px"},
    "& .docs-md-link, & .docs-md-link:hover": {"color": "var(--gray-12)", "text_decoration": "none"},
    # article body: code blocks framed like the rest, their copy button quiet until hovered
    "& .docs-article pre": {"border": "1px solid var(--gray-a4)", "border_radius": "var(--radius-3) !important",
                            "font_size": "13px"},
    "& .docs-article pre + button[aria-label='Copy code']": {
        "background": "var(--color-panel-solid) !important", "color": "var(--gray-11) !important",
        "box_shadow": "inset 0 0 0 1px var(--gray-a5)", "width": "28px", "height": "28px", "padding": "0",
        "top": "16px !important", "right": "8px !important", "opacity": "0.75"},
    "& .docs-article pre + button[aria-label='Copy code']:hover": {"opacity": "1"},
    "& .docs-article pre + button[aria-label='Copy code'] svg": {"width": "15px", "height": "15px"},
    "& .docs-article li": {"line_height": "1.7"},
    "& .docs-article-wide": {"max_width": "1040px"},
    "& .docs-lede": {"margin": "8px 0 28px", "color": "var(--gray-11)", "font_size": "var(--font-size-4)",
                     "line_height": "1.55"},
    "& .docs-title": {"letter_spacing": "-0.02em"},
    # previous / next
    "& .docs-pager": {"display": "grid", "grid_template_columns": "1fr", "gap": "12px", "margin_top": "56px",
                      "padding_top": "24px", "border_top": "1px solid var(--gray-a5)"},
    "& .docs-pager-card": {"display": "flex", "flex_direction": "column", "gap": "2px", "padding": "12px 16px",
                           "border": "1px solid var(--gray-a5)", "border_radius": "var(--radius-3)",
                           "text_decoration": "none", "transition": "border-color 120ms, background 120ms"},
    "& .docs-pager-card:hover": {"border_color": "var(--accent-a8)", "background": "var(--accent-a2)"},
    "& .docs-pager-next": {"text_align": "right"},
    "& .docs-pager-label": {"font_size": "var(--font-size-1)", "color": "var(--gray-10)"},
    "& .docs-pager-title": {"font_size": "var(--font-size-3)", "font_weight": "500", "color": "var(--accent-11)"},
    # landing
    "& .docs-hero p": {"max_width": "720px"},
    "& .docs-hero": {"padding": "16px 0 32px", "margin_bottom": "8px", "border_bottom": "1px solid var(--gray-a5)"},
    "& .docs-hero-eyebrow": {"font_size": "var(--font-size-2)", "font_weight": "600", "color": "var(--accent-11)",
                             "margin": "0 0 8px"},
    "& .docs-section-card": {"display": "flex", "flex_direction": "column", "gap": "6px", "height": "100%",
                             "box_sizing": "border-box", "padding": "18px 20px", "border": "1px solid var(--gray-a5)",
                             "border_radius": "var(--radius-4)", "background": "var(--color-panel-solid)",
                             "text_decoration": "none", "color": "inherit",
                             "transition": "border-color 120ms, box-shadow 120ms, transform 120ms"},
    "& .docs-section-card:hover": {"border_color": "var(--accent-a8)", "box_shadow": "var(--shadow-3)"},
    "& .docs-section-card-title": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                                   "font_size": "var(--font-size-4)", "font_weight": "600",
                                   "color": "var(--gray-12)"},
    "& .docs-section-card-blurb": {"font_size": "var(--font-size-2)", "line_height": "1.5",
                                   "color": "var(--gray-11)"},
    "& .docs-section-card-count": {"margin_top": "auto", "padding_top": "8px", "font_size": "var(--font-size-1)",
                                   "color": "var(--gray-10)"},
    "@media (min-width: 640px)": {
        "& .docs-product-sub": {"display": "inline"},
        "& .docs-pager": {"grid_template_columns": "1fr 1fr"},
        "& .docs-main": {"padding": "32px 32px 80px"},
    },
    f"@media (min-width: {SIDEBAR_FROM})": {
        "& .docs-grid": {"grid_template_columns": "var(--docs-sidebar-w) minmax(0, 1fr)"},
        "& .rfds-header-menu-slot": {"display": "none"},
        "& .docs-sidebar": {"position": "sticky", "top": HEADER, "align_self": "start", "width": "auto",
                            "height": f"calc(100dvh - {HEADER})", "visibility": "visible", "transform": "none",
                            "box_shadow": "none", "background": "transparent", "z_index": "auto",
                            "border_right": "1px solid var(--gray-a5)", "padding": "24px 12px 48px 16px",
                            "transition": "none"},
        "& .docs-main": {"padding": "40px 48px 96px"},
    },
    f"@media (min-width: {TOC_FROM})": {
        "& .docs-grid": {"grid_template_columns": "var(--docs-sidebar-w) minmax(0, 1fr) var(--docs-toc-w)"},
        "& .docs-grid > .docs-main:last-child": {"grid_column": "span 2"},
        "& .docs-toc": {"display": "block", "position": "sticky", "top": HEADER, "align_self": "start",
                        "max_height": f"calc(100dvh - {HEADER})", "overflow_y": "auto",
                        "padding": "48px 16px 32px 0"},
    },
}


def shell(slug: str, body: rx.Component, *, aside: rx.Component | None = None, wide: bool = False) -> rx.Component:
    """The frame around ``body``; ``aside`` is the "On this page" column, ``wide`` lets a page
    without one (the landing page) use that column's room too."""
    toc_column = rx.el.aside(aside, class_name="docs-toc") if aside is not None else rx.fragment()
    return rx.el.div(
        _header(),
        rx.el.div(
            _sidebar(slug),
            rx.el.main(rx.el.article(body, class_name="docs-article" + (" docs-article-wide" if wide else "")),
                       id="main", class_name="docs-main"),
            toc_column,
            class_name="docs-grid",
        ),
        class_name="rfds-shell docs-shell",
        custom_attrs={"data-rfds-shell": "root", "data-rfds-has-nav": "true"},
        style=_STYLE,
    )


def _content_page(slug: str):
    def page() -> rx.Component:
        doc = load(slug)
        section = nav.section_of(slug)
        first = section.groups[0].pages[0]
        body = rx.box(
            rx.el.div(
                rfds.breadcrumbs({"label": section.title, "href": route_of(first)}, {"label": doc.title}),
                _page_actions(slug),
                class_name="docs-article-head",
            ),
            rx.heading(doc.title, size="8", as_="h1", class_name="docs-title"),
            rx.text(doc.description, as_="p", class_name="docs-lede") if doc.description
            else rx.box(height="20px"),
            render_page(doc),
            _prev_next(slug),
        )
        return shell(slug, body, aside=toc(doc))
    page.__name__ = page.__qualname__ = "page_" + slug.replace("/", "_").replace("-", "_")
    return page


def _section_card(section: nav.Section) -> rx.Component:
    count = sum(len(g.pages) for g in section.groups)
    return rx.link(
        rx.el.span(section.title, rfds.icon("arrow_right", size="sm"), class_name="docs-section-card-title"),
        rx.el.span(section.blurb, class_name="docs-section-card-blurb"),
        rx.el.span(f"{count} page{'s' if count != 1 else ''}", class_name="docs-section-card-count"),
        href=route_of(section.groups[0].pages[0]), underline="none", class_name="docs-section-card",
    )


def _landing() -> rx.Component:
    doc = load("index")
    hero = rx.box(
        rx.el.p("Documentation", class_name="docs-hero-eyebrow"),
        rx.heading(doc.title, size=rx.breakpoints(initial="8", md="9"), as_="h1", class_name="docs-title"),
        rx.text(doc.description, as_="p", class_name="docs-lede"),
        render_page(doc),
        class_name="docs-hero",
    )
    cards = rx.grid(*[_section_card(s) for s in nav.NAV],
                    columns=rx.breakpoints(initial="1", sm="2", xl="3"), spacing="4", margin_top="24px")
    return shell("index", rx.box(hero, cards), wide=True)


def add_pages(app: rx.App) -> None:
    app.add_page(_landing, route="/", title=load("index").title, description=load("index").description)
    for slug in nav.ordered():
        doc = load(slug)
        app.add_page(_content_page(slug), route=route_of(slug), title=f"{doc.title} · lex-app dashboards",
                     description=doc.description)
