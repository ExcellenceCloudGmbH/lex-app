"""The site's frame: header, sidebar, the page, "On this page", previous and next.

The frame is a centred grid under RFDS's header: the sidebar (from 1024 px; below that a drawer
behind the header's menu button), the article, and "On this page" (from 1280 px). Its colours are
the tokens of :mod:`docs_site.theme` (``var(--docs-...)``), which follow Reflex's colour mode; the
lex.ui demos keep RFDS's own tokens.
"""

import re

import reflex as rx
import rfds

from docs_site import llms, nav, reference
from docs_site.content import load
from docs_site.render import render_page, toc
from docs_site.theme import VARS, var

#: the header's height (RFDS's shell token, set to 60px on the frame): sticky columns sit right below it
HEADER = "var(--rfds-size-shell-header, 60px)"
SIDEBAR_FROM = "1024px"     # the sidebar is a column from here, a drawer below
TOC_FROM = "1280px"         # "On this page" is a column from here, hidden below
MONO = "'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, monospace"
#: Inter is lex-app's own font (lex_head_components); the code font is the docs' addition
MONO_STYLESHEET = "https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;500&display=swap"

#: a chapter's icon in the sidebar and on the landing cards (presentation only: nav.py holds the order)
SECTION_ICONS = {
    "start-here": "flag", "show-your-data": "table", "let-people-act": "mouse_pointer_click",
    "long-running-work": "hourglass", "who-can-do-what": "shield_check", "make-it-your-app": "panels_top_left",
    "test-and-ship": "rocket", "reference": "braces",
}


def route_of(slug: str) -> str:
    return "/" if slug == "index" else "/" + slug


def _title(slug: str) -> str:
    return load(slug).title


def _section(slug: str) -> str:
    return "" if slug == "index" else nav.section_of(slug).slug


def chapter_label(section: nav.Section) -> str:
    """"Chapter 2 · Show your data"; the API reference is not a chapter, and says its own name."""
    n = nav.number(section)
    return section.title if n is None else f"Chapter {n} · {section.title}"


def api_href(name: str) -> str | None:
    """The API reference's entry for a name in a page's ``api:`` (``@ui.page`` is the ``ui.page``
    entry), or None when the reference has none (``self.api``, ``lex reflex check``)."""
    bare = name.removeprefix("@")
    if not bare.startswith("ui."):
        return None
    for group, names in reference.GROUPS.items():
        if bare.removeprefix("ui.") in names:
            slug = f"reference/{group}"
            for _, text, anchor_id in load(slug).headings:
                if text == bare:
                    return f"{route_of(slug)}#{anchor_id}"
    return None


# -- header -------------------------------------------------------------------------------------
def _mark() -> rx.Component:
    return rx.box(rfds.icon("layout_grid", size="sm", stroke=2.2), class_name="docs-mark",
                  custom_attrs={"aria-hidden": "true"})


#: the header's tabs: "Guide" is the landing and the seven chapters, "API reference" the reference
_REFERENCE_SLUG = nav.REFERENCE


def _tabs(current: str) -> rx.Component:
    here = _section(current)
    reference_section = next(s for s in nav.NAV if s.slug == _REFERENCE_SLUG)
    tabs = (
        ("Guide", "/", here != _REFERENCE_SLUG),
        ("API reference", route_of(nav.first(reference_section)), here == _REFERENCE_SLUG),
    )
    return rx.el.nav(
        *[rx.link(label, href=href, underline="none", class_name="docs-tab",
                  aria_current="true" if on else None) for label, href, on in tabs],
        aria_label="Sections", class_name="docs-tabs",
    )


def _header(current: str = "index") -> rx.Component:
    from docs_site import search

    product = rx.link(_mark(), rx.el.span("lex-app", class_name="docs-product-name"),
                      rx.el.span("dashboards", class_name="docs-product-sub"),
                      href="/", underline="none", class_name="docs-product")
    return rfds.header(product=rx.fragment(product, _tabs(current)), actions=[search.box(), mode_toggle()],
                       job_tray=False)


def mode_toggle() -> rx.Component:
    """Light / dark: Reflex's colour mode (stored in the browser, so it holds across pages); the
    Radix theme, RFDS's tokens and this site's (``.dark &`` in theme.VARS) follow the class it sets."""
    def button(icon: str, label: str) -> rx.Component:
        return rfds.icon_button(icon, label=label, tooltip=False, on_click=rx.toggle_color_mode,
                                class_name="docs-mode-toggle")

    return rx.color_mode_cond(light=button("moon", "Switch to dark mode"),
                              dark=button("sun", "Switch to light mode"))


# -- sidebar ------------------------------------------------------------------------------------
_GUIDE = nav.CHAPTERS
_REFERENCE = tuple(s for s in nav.NAV if s.slug == _REFERENCE_SLUG)


def _nav_link(slug: str, current: str) -> rx.Component:
    here = slug == current
    return rx.el.li(rx.link(_title(slug), href=route_of(slug), underline="none",
                            class_name="docs-nav-link", aria_current="page" if here else None))


def _nav_section(section: nav.Section, current: str) -> rx.Component:
    """One chapter as a list that opens and closes (a native details: client-side, and the
    browser says it is expanded); the current page's chapter starts open. A chapter is one list
    of pages, in the story's order: no group labels inside it."""
    pages = nav.pages_of(section)
    n = nav.number(section)
    title = [rx.el.span(str(n), class_name="docs-nav-num"), section.title] if n is not None else [section.title]
    return rx.el.details(
        rx.el.summary(rx.el.span(rfds.icon(SECTION_ICONS.get(section.slug, "file_text"), size="sm"),
                                 class_name="docs-nav-icon"),
                      rx.el.span(*title, class_name="docs-nav-title"),
                      rx.el.span(str(len(pages)), class_name="docs-nav-count", custom_attrs={"aria-hidden": "true"}),
                      rfds.icon("chevron_right", size="sm", class_name="docs-nav-chevron"),
                      class_name="docs-nav-summary"),
        rx.el.ul(*[_nav_link(slug, current) for slug in pages], class_name="docs-nav-list"),
        open=current in pages, class_name="docs-nav-section",
    )


def _sidebar(current: str) -> rx.Component:
    def block(heading: str, sections: tuple[nav.Section, ...]) -> rx.Component:
        return rx.box(rx.el.p(heading, class_name="docs-nav-heading"),
                      *[_nav_section(s, current) for s in sections], class_name="docs-nav-block")

    # id and data-rfds-shell are what RFDS's header menu button opens below 1024 px (drawer)
    return rx.el.nav(block("Guide", _GUIDE), block("Reference", _REFERENCE),
                     aria_label="Docs", id="rfds-side-nav", class_name="docs-sidebar",
                     custom_attrs={"data-rfds-shell": "sidebar"})


# -- article parts ------------------------------------------------------------------------------
def _page_actions(slug: str) -> rx.Component:
    """"Copy page" | "Markdown": the page as Markdown, to the clipboard or as a file."""
    markdown = f"{rx.config.get_config().api_url}/md/{slug}.md"
    return rx.el.div(
        rx.el.button(rfds.icon("copy", size="sm"), "Copy page", type="button", class_name="docs-action",
                     on_click=rx.set_clipboard(llms.page_markdown(slug))),
        rx.el.a(rfds.icon("file_text", size="sm"), "Markdown", href=markdown, target="_blank",
                rel="noopener", class_name="docs-action",
                custom_attrs={"aria-label": "View this page as Markdown"}),
        class_name="docs-page-actions", custom_attrs={"role": "group", "aria-label": "Page actions"},
    )


def _meta(slug: str) -> rx.Component:
    """Reading time, and "Every example runs" on a page with live demos."""
    source = load(slug).source
    words = len(re.findall(r"\w+", source))
    items = [rx.el.span(rfds.icon("clock", size="sm"), f"{max(1, round(words / 220))} min read")]
    if re.search(r"^```python demo", source, re.M):
        items.append(rx.el.span(rfds.icon("check", size="sm"), "Every example runs"))
    return rx.el.div(*items, class_name="docs-meta")


def _api_tags(slug: str) -> rx.Component:
    """"In the API": the names the page teaches, each a link to its API reference entry when the
    reference has one; the title says what the page does, these say what it is called in code."""
    names = load(slug).api
    if not names:
        return rx.fragment()

    def tag(name: str) -> rx.Component:
        code = rx.el.code(name, class_name="docs-api-name")
        href = api_href(name)
        if href is None:
            return rx.el.li(code, class_name="docs-api-tag")
        return rx.el.li(rx.link(code, href=href, underline="none", class_name="docs-api-link",
                                custom_attrs={"aria-label": f"{name} in the API reference"}),
                        class_name="docs-api-tag")

    return rx.el.div(
        rx.el.span("In the API", class_name="docs-api-label", id=f"docs-api-{slug.replace('/', '-')}"),
        rx.el.ul(*[tag(n) for n in names], class_name="docs-api-list",
                 custom_attrs={"aria-labelledby": f"docs-api-{slug.replace('/', '-')}"}),
        class_name="docs-api",
    )


def _chapter_intro(slug: str) -> rx.Component:
    """On a chapter's first page: the chapter's part of the story, before the page begins."""
    section = nav.section_of(slug)
    n = nav.number(section)
    if n is None or nav.first(section) != slug:
        return rx.fragment()
    return rx.el.aside(
        rx.el.span(rfds.icon(SECTION_ICONS.get(section.slug, "file_text"), size="md"),
                   class_name="docs-intro-icon", custom_attrs={"aria-hidden": "true"}),
        rx.el.div(
            rx.el.p(f"Chapter {n} of {len(nav.CHAPTERS)} · {section.title}", class_name="docs-intro-label"),
            rx.el.p(section.story, class_name="docs-intro-story"),
            class_name="docs-intro-body",
        ),
        class_name="docs-intro", custom_attrs={"aria-label": "This chapter"},
    )


def _prev_next(slug: str) -> rx.Component:
    before, after = nav.neighbours(slug)

    def card(target: str | None, label: str, end: bool) -> rx.Component:
        if target is None:
            return rx.box(class_name="docs-pager-empty")
        arrow = rfds.icon("arrow_right" if end else "arrow_left", size="sm")
        title = [_title(target), arrow] if end else [arrow, _title(target)]
        return rx.link(rx.el.span(label, class_name="docs-pager-label"),
                       rx.el.span(*title, class_name="docs-pager-title"),
                       href=route_of(target), underline="none",
                       class_name="docs-pager-card" + (" docs-pager-next" if end else ""))

    return rx.el.nav(card(before, "Previous", False), card(after, "Next", True),
                     aria_label="Previous and next page", class_name="docs-pager")


# -- the frame ----------------------------------------------------------------------------------
_TEXT = {"font_family": "Inter, system-ui, sans-serif"}

_STYLE = {
    **VARS,
    "--docs-sidebar-w": "304px",       # the chapters' page titles fit on one line
    "--docs-toc-w": "232px",
    "--docs-mono": MONO,
    "--rfds-size-shell-header": "60px",
    # the header through RFDS's chrome tokens and lex-app's header tokens: translucent and blurred
    "--rfds-color-chrome-bg": var("bg"),
    "--rfds-color-chrome-fg": var("ink"),
    "--rfds-color-chrome-hover": var("hover"),
    "--rfds-color-chrome-selected": var("hover"),
    "--rfds-color-chrome-border": var("border"),
    "--rfds-color-chrome-stripe": var("border"),
    "--rfds-color-focus-ring-inverse": "var(--rfds-color-focus-ring, var(--accent-8))",
    # lex-app's theme paints the band through these two (html:root:root .rfds-header)
    "--lex-header-bg": var("header"),
    "--lex-header-shadow": f"inset 0 -1px 0 {var('border')}",
    "&.rfds-shell": {"background_color": var("bg"), "color": var("ink"), **_TEXT},
    "& .docs-prose a.rt-Link": {"color": var("link")},
    "& .docs-prose a.rt-Link:hover": {"color": var("link-hover")},
    "& .docs-results a.rt-Link": {"color": var("ink")},
    # header: the product, the section tabs, search and the mode button
    "& .rfds-header": {"padding_inline": "max(12px, calc((100% - 1440px) / 2 + 24px))",
                       "backdrop_filter": "saturate(1.4) blur(10px)", "gap": "12px"},
    "& .rfds-header-brand": {"gap": "28px"},
    "& .rfds-header-actions": {"margin_inline_start": "auto", "gap": "10px"},
    "& .docs-product": {"display": "inline-flex", "align_items": "center", "gap": "10px", "color": var("ink"),
                        "white_space": "nowrap"},
    "& .docs-mark": {"display": "inline-flex", "align_items": "center", "justify_content": "center",
                     "width": "28px", "height": "28px", "border_radius": "8px", "flex": "none",
                     "background": var("accent"), "color": var("on-accent")},
    "& .docs-product-name": {"font_weight": "700", "font_size": "16px", "letter_spacing": "-0.01em"},
    "& .docs-product-sub": {"display": "none", "font_size": "13px", "font_weight": "500", "color": var("muted"),
                            "padding": "2px 8px", "border": f"1px solid {var('border')}", "border_radius": "999px",
                            "line_height": "18px"},
    "& .docs-tabs": {"display": "none", "gap": "4px"},
    "& .docs-tab": {"padding": "6px 12px", "border_radius": "8px", "font_size": "14px", "font_weight": "500",
                    "line_height": "20px", "color": var("muted"), "text_decoration": "none", "white_space": "nowrap"},
    "& .docs-tab:hover": {"color": var("ink"), "background": var("hover")},
    "& .docs-tab[aria-current]": {"color": var("ink"), "font_weight": "600", "background": var("hover")},
    "& .docs-search .rt-TextFieldRoot": {"height": "36px", "border_radius": "10px", "background": var("sunk"),
                                         "box_shadow": f"inset 0 0 0 1px {var('border')}", "color": var("muted"),
                                         "font_size": "14px"},
    "& .docs-search .rt-TextFieldRoot:focus-within": {"box_shadow": f"inset 0 0 0 1px {var('link')}, "
                                                                    f"0 0 0 3px {var('pill-bg')}"},
    "& .docs-search .rt-TextFieldInput": {"color": var("ink"), "font_size": "14px"},
    "& .docs-search .rt-TextFieldInput::placeholder": {"color": var("muted")},
    "& .docs-kbd": {"display": "none", "font_family": "Inter, sans-serif", "font_size": "11px", "font_weight": "600",
                    "padding": "2px 6px", "border_radius": "6px", "border": f"1px solid {var('border')}",
                    "background": var("surface"), "color": var("muted"), "line_height": "14px",
                    "white_space": "nowrap"},
    "& .docs-results": {"background": var("surface"), "border": f"1px solid {var('border')}"},
    "& .docs-results a:hover": {"background": var("hover")},
    "& .docs-mode-toggle.rt-IconButton": {"width": "36px", "height": "36px", "border_radius": "10px",
                                          "background": var("surface"), "color": var("ink"),
                                          "box_shadow": f"inset 0 0 0 1px {var('border')}"},
    "& .docs-mode-toggle.rt-IconButton:hover": {"background": var("hover")},
    # the grid
    "& .docs-grid": {"display": "grid", "grid_template_columns": "minmax(0, 1fr)", "width": "100%",
                     "max_width": "1440px", "margin": "0 auto", "flex": "1 1 auto"},
    "& .docs-main": {"min_width": "0", "padding": "24px 16px 64px"},
    "& .docs-article": {"max_width": "760px", "margin": "0 auto", "width": "100%"},
    # sidebar (a drawer below 1024 px)
    "& .docs-sidebar": {"box_sizing": "border-box", "position": "fixed", "top": HEADER, "bottom": "0", "left": "0",
                        "width": "min(300px, 85vw)", "overflow_y": "auto", "padding": "24px 16px 40px",
                        "background": var("side"), "box_shadow": "var(--shadow-5)",
                        "border_right": f"1px solid {var('border')}",
                        "visibility": "hidden", "transform": "translateX(-100%)", "z_index": "40",
                        "transition": "transform 160ms ease, visibility 160ms"},
    "&[data-rfds-nav-open='true'] .docs-sidebar": {"visibility": "visible", "transform": "none"},
    "& .rfds-header-menu-slot": {"display": "flex"},
    "& .docs-nav-block": {"display": "flex", "flex_direction": "column", "gap": "2px"},
    "& .docs-nav-block + .docs-nav-block": {"margin_top": "20px"},
    "& .docs-nav-heading": {"margin": "0", "padding": "0 10px 8px", "font_size": "11px", "font_weight": "600",
                            "letter_spacing": "0.08em", "text_transform": "uppercase", "color": var("muted")},
    "& .docs-nav-summary": {"display": "flex", "align_items": "center", "gap": "10px", "height": "36px",
                            "padding": "0 10px", "border_radius": "9px", "cursor": "pointer", "list_style": "none",
                            "font_size": "14px", "font_weight": "550", "color": var("ink"), "user_select": "none"},
    "& .docs-nav-summary::-webkit-details-marker": {"display": "none"},
    "& .docs-nav-summary:hover": {"background": var("hover")},
    "& .docs-nav-icon": {"display": "flex", "color": var("muted")},
    "& .docs-nav-title": {"flex": "1", "min_width": "0"},
    "& .docs-nav-count": {"font_size": "12px", "font_weight": "400", "color": var("muted")},
    "& .docs-nav-chevron": {"color": var("muted"), "transition": "transform 150ms ease", "flex": "none"},
    "& .docs-nav-section[open] > .docs-nav-summary .docs-nav-chevron": {"transform": "rotate(90deg)"},
    "& .docs-nav-list": {"list_style": "none", "margin": "2px 0 8px 22px", "padding": "0 0 0 10px",
                         "border_left": f"1px solid {var('border')}", "display": "flex", "flex_direction": "column",
                         "gap": "1px"},
    "& .docs-nav-num": {"display": "inline-block", "min_width": "16px", "margin_right": "6px",
                        "font_variant_numeric": "tabular-nums", "font_weight": "600", "color": var("muted")},
    "& .docs-nav-link": {"display": "flex", "align_items": "center", "justify_content": "space-between", "gap": "8px",
                         "margin_left": "-11px", "padding": "6px 10px 6px 12px",
                         "border_left": "2px solid transparent", "border_radius": "0 8px 8px 0",
                         "font_size": "14px", "font_weight": "450", "line_height": "20px",
                         "color": var("item-ink"), "text_decoration": "none"},
    "& .docs-nav-link:hover": {"background": var("hover"), "color": var("ink")},
    "& .docs-nav-link[aria-current='page']": {"background": var("pill-bg"), "color": var("link"),
                                              "font_weight": "600", "border_left_color": var("accent")},
    # "On this page"
    "& .docs-toc": {"display": "none"},
    "& .docs-toc-title": {"display": "flex", "align_items": "center", "gap": "8px", "margin": "0 0 12px",
                          "font_size": "12px", "font_weight": "600", "letter_spacing": "0.06em",
                          "text_transform": "uppercase", "color": var("muted")},
    "& .docs-toc .rfds-anchor-nav-list": {"gap": "0", "border_inline_start": f"1px solid {var('border')}"},
    "& .docs-toc .rfds-anchor-nav-link": {"margin_left": "-1px", "padding": "5px 12px", "font_size": "13px",
                                          "line_height": "1.5", "color": var("muted"),
                                          "border_left": "2px solid transparent", "box_shadow": "none"},
    "& .docs-toc .rfds-anchor-nav-link:hover": {"color": var("ink"), "text_decoration": "none"},
    "& .docs-toc .rfds-anchor-nav-link[aria-current='location']": {"color": var("link"), "font_weight": "600",
                                                                   "border_left_color": var("accent"),
                                                                   "box_shadow": "none"},
    "& .docs-toc .rfds-anchor-nav-link:has(.docs-toc-h3)": {"padding_left": "24px"},
    # article header: breadcrumb and the page actions on one row, title, lead, reading time
    "& .docs-article-head": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                             "flex_wrap": "wrap", "gap": "12px 16px", "margin_bottom": "18px"},
    "& .docs-article-head .rfds-breadcrumbs": {"font_size": "13px", "color": var("muted")},
    "& .docs-article-head .rfds-breadcrumbs-link": {"color": var("muted"), "font_size": "13px"},
    "& .docs-article-head .rfds-breadcrumbs-link:hover": {"color": var("ink")},
    "& .docs-article-head .rfds-breadcrumbs-sep": {"color": var("muted")},
    "& .docs-article-head .rfds-breadcrumbs-current": {"color": var("ink"), "font_weight": "500",
                                                       "font_size": "13px"},
    "& .docs-page-actions": {"display": "flex", "border": f"1px solid {var('border')}", "border_radius": "10px",
                             "overflow": "hidden", "background": var("surface")},
    "& .docs-action": {"display": "flex", "align_items": "center", "gap": "6px", "height": "32px", "padding": "0 12px",
                       "border": "0", "background": "transparent", "font": "inherit", "font_size": "13px",
                       "font_weight": "500", "color": var("ink"), "cursor": "pointer", "text_decoration": "none"},
    "& .docs-action + .docs-action": {"border_left": f"1px solid {var('border')}"},
    "& .docs-action:hover": {"background": var("hover"), "color": var("ink")},
    "& .docs-title.rt-Heading": {"margin": "0", "font_size": "32px", "line_height": "1.15", "font_weight": "700",
                                 "letter_spacing": "-0.025em", "color": var("ink")},
    "& .docs-lede": {"margin": "12px 0 0", "color": var("muted"), "font_size": "18px", "line_height": "1.6"},
    "& .docs-meta": {"display": "flex", "flex_wrap": "wrap", "gap": "16px", "margin": "18px 0 32px",
                     "font_size": "13px", "color": var("muted")},
    "& .docs-meta > span": {"display": "flex", "align_items": "center", "gap": "6px"},
    # "In the API": the page's API names as small code tags, under the lead
    "& .docs-api": {"display": "flex", "flex_wrap": "wrap", "align_items": "center", "gap": "8px 10px",
                    "margin": "16px 0 0"},
    "& .docs-api + .docs-meta": {"margin_top": "14px"},
    "& .docs-api-label": {"font_size": "13px", "font_weight": "500", "color": var("muted")},
    "& .docs-api-list": {"display": "flex", "flex_wrap": "wrap", "gap": "6px", "margin": "0", "padding": "0",
                         "list_style": "none"},
    "& .docs-api-name": {"display": "inline-block", "font_family": "var(--docs-mono)", "font_size": "12.5px",
                         "line_height": "20px", "padding": "1px 8px", "border_radius": "6px",
                         "background": var("pill-bg"), "color": var("pill-ink"),
                         "border": f"1px solid {var('pill-bg')}", "font_variant_ligatures": "none"},
    "& .docs-api-link": {"text_decoration": "none"},
    "& .docs-api-link:hover .docs-api-name": {"border_color": var("accent")},
    # a chapter's first page: the chapter's part of the story, before the page begins
    "& .docs-intro": {"display": "flex", "gap": "14px", "align_items": "flex-start", "margin": "0 0 32px",
                      "padding": "16px 18px", "border_radius": "12px", "background": var("sunk"),
                      "border": f"1px solid {var('border')}", "border_left": f"3px solid {var('accent')}"},
    "& .docs-intro-icon": {"display": "none", "align_items": "center", "justify_content": "center",
                           "flex": "none", "width": "32px", "height": "32px", "border_radius": "8px",
                           "background": var("pill-bg"), "color": var("pill-ink")},
    "& .docs-intro-body": {"display": "flex", "flex_direction": "column", "gap": "2px", "min_width": "0"},
    "& .docs-intro-label": {"margin": "0", "font_size": "12px", "font_weight": "600", "letter_spacing": "0.04em",
                            "text_transform": "uppercase", "color": var("link")},
    "& .docs-intro-story": {"margin": "0", "font_size": "16px", "line_height": "1.55", "color": var("ink")},
    # article body
    "& .docs-prose": {"font_size": "15px", "line_height": "1.65", "color": var("ink")},
    "& .docs-prose p.rt-Text, & .docs-prose li": {"font_size": "15px", "line_height": "1.65"},
    "& .docs-prose h2.rt-Heading": {"font_size": "24px", "line_height": "1.3", "font_weight": "650",
                                    "letter_spacing": "-0.015em", "margin_bottom": "-4px"},
    "& .docs-prose h3.rt-Heading": {"font_size": "18px", "line_height": "1.4", "font_weight": "650",
                                    "margin_bottom": "-4px"},
    "& .docs-prose :not(pre) > code.docs-code-span": {"font_family": "var(--docs-mono)", "font_size": "13px",
                                                      "padding": "2px 6px", "border_radius": "6px",
                                                      "background": var("pill-bg"), "color": var("pill-ink"),
                                                      "white_space": "break-spaces"},
    "& .docs-article-wide": {"max_width": "1040px"},
    # code blocks: a header strip (icon, language or file name, copy button) over the code
    "& .docs-code": {"margin": "0", "border": f"1px solid {var('border')}", "border_radius": "12px",
                     "overflow": "hidden", "background": var("code-bg"), "min_width": "0"},
    "& .docs-code-head": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                          "height": "40px", "padding": "0 8px 0 16px", "border_bottom": f"1px solid {var('border')}",
                          "color": var("muted")},
    "& .docs-code-label": {"display": "flex", "align_items": "center", "gap": "8px",
                           "font_family": "var(--docs-mono)", "font_size": "12px"},
    "& .docs-code-copy.rt-IconButton": {"width": "30px", "height": "30px", "border_radius": "8px",
                                        "color": var("muted"), "background": "transparent"},
    "& .docs-code-copy.rt-IconButton:hover": {"background": var("hover"), "color": var("ink")},
    "& .docs-code pre": {"margin": "0 !important", "padding": "16px 18px !important",
                         "background": "transparent !important", "border": "0", "border_radius": "0",
                         "overflow_x": "auto", "font_size": "13px !important", "line_height": "1.7 !important"},
    "& .docs-code pre code, & .docs-demo-code pre code, & .docs-code-span": {"font_variant_ligatures": "none"},
    "& .docs-code pre code": {"font_family": "var(--docs-mono) !important", "font_size": "13px !important",
                              "line_height": "1.7 !important", "background": "transparent !important"},
    # callouts (```md alert```): a tinted panel with an icon
    "& .docs-callout": {"display": "flex", "gap": "12px", "padding": "14px 16px", "border_radius": "12px",
                        "border": "1px solid"},
    "& .docs-callout-icon": {"display": "flex", "flex": "none", "margin_top": "3px"},
    "& .docs-callout-body": {"flex": "1", "min_width": "0", "display": "flex", "flex_direction": "column",
                             "gap": "8px"},
    "& .docs-callout-body p.rt-Text, & .docs-callout-body li": {"font_size": "14px", "line_height": "1.6",
                                                                "color": "inherit"},
    "& .docs-callout-body strong": {"font_weight": "600"},
    "& .docs-callout a.rt-Link": {"color": "inherit", "text_decoration": "underline"},
    **{f"& .docs-callout-{tone}": {"background": var(f"{tone}-bg"), "border_color": var(f"{tone}-border"),
                                   "color": var(f"{tone}-ink")}
       for tone in ("info", "success", "warning", "danger")},
    # demo card: toolbar (Preview / Code, Viewing as, Reset) over the preview or the code
    "& .docs-demo": {"border": f"1px solid {var('border')}", "border_radius": "14px", "overflow": "hidden",
                     "background": var("surface"), "min_width": "0"},
    "& .docs-demo-bar": {"display": "flex", "align_items": "center", "justify_content": "space-between",
                         "gap": "8px 12px", "flex_wrap": "wrap", "padding": "8px 10px 8px 8px",
                         "border_bottom": f"1px solid {var('border')}", "background": var("sunk")},
    "& .docs-demo-tabs.rt-TabsList": {"box_shadow": "none", "gap": "2px", "height": "auto"},
    "& .docs-demo-tab": {"display": "inline-flex", "align_items": "center", "height": "32px", "padding": "0 14px",
                         "border_radius": "8px", "border": "1px solid transparent", "background": "transparent",
                         "font_size": "13px", "font_weight": "600", "color": var("muted"), "cursor": "pointer",
                         "box_sizing": "border-box"},
    "& .docs-demo-tab::before": {"display": "none"},
    "& .docs-demo-tab .rt-BaseTabListTriggerInner, & .docs-demo-tab .rt-TabsTriggerInner": {
        "padding": "0", "background": "transparent !important"},
    "& .docs-demo-tab:hover": {"color": var("ink")},
    "& .docs-demo-tab[data-state='active'], & .docs-demo-tab-static": {
        "background": var("surface"), "border_color": var("border"), "color": var("ink")},
    "& .docs-demo-tab-static": {"cursor": "default"},
    "& .docs-demo-controls": {"display": "flex", "align_items": "center", "gap": "10px", "flex_wrap": "wrap"},
    "& .docs-demo-as": {"display": "none", "font_size": "12px", "color": var("muted")},
    "& .docs-demo .rfds-segmented": {"--segmented-control-indicator-background-color": var("on-bg"),
                                     "background_color": var("hover"), "background_image": "none",
                                     "border_radius": "9px", "height": "32px"},
    "& .docs-demo .rfds-segmented .rt-SegmentedControlIndicator::before": {"inset": "2px", "border_radius": "7px",
                                                                           "box_shadow": "var(--shadow-1)"},
    "& .docs-demo .rfds-segmented .rt-SegmentedControlItem": {"font_size": "13px", "font_weight": "600",
                                                              "padding": "0 12px", "color": var("muted")},
    "& .docs-demo .rfds-segmented .rt-SegmentedControlItem[data-state='on']": {"color": var("on-ink")},
    "& .docs-demo .rfds-segmented .rt-SegmentedControlItemSeparator": {"display": "none"},
    "& .docs-demo-reset.rt-Button": {"height": "32px", "padding": "0 10px", "border_radius": "8px", "gap": "6px",
                                     "font_size": "13px", "font_weight": "500", "color": var("ink"),
                                     "background": var("surface"),
                                     "box_shadow": f"inset 0 0 0 1px {var('border')}"},
    "& .docs-demo-reset.rt-Button:hover": {"background": var("hover")},
    "& .docs-demo-pane[data-state='inactive']": {"display": "none"},
    "& .docs-demo-frame": {"padding": "22px", "min_width": "0"},
    "& .docs-demo-code pre": {"margin": "0 !important", "padding": "18px !important",
                              "background": f"{var('code-bg')} !important", "overflow_x": "auto",
                              "font_size": "13px !important", "line_height": "1.7 !important"},
    "& .docs-demo-code pre code": {"font_family": "var(--docs-mono) !important", "font_size": "13px !important",
                                   "line_height": "1.7 !important", "background": "transparent !important"},
    # the lex.ui table inside a demo: shaded header, quiet rules (RFDS's own table, restyled from outside)
    "& .docs-demo-frame .rfds-page-header": {"background": "transparent", "padding": "0 0 14px"},
    "& .docs-demo-frame .rfds-page-title.rt-Heading": {"font_size": "18px", "font_weight": "650",
                                                       "line_height": "1.4"},
    "& .docs-demo-frame .rfds-data-table-table": {"--table-border-color": var("border")},
    "& .docs-demo-frame .rfds-data-table-header, & .docs-demo-frame thead th": {"background": var("sunk"), "font_size": "12px",
                                                   "font_weight": "600", "color": var("muted")},
    "& .docs-demo-frame .rfds-data-table-header .rfds-data-table-header-text": {"color": var("muted")},
    # previous / next
    "& .docs-pager": {"display": "grid", "grid_template_columns": "1fr", "gap": "16px", "margin_top": "56px",
                      "padding_top": "28px", "border_top": f"1px solid {var('border')}"},
    "& .docs-pager-card": {"display": "flex", "flex_direction": "column", "align_items": "flex-start", "gap": "2px",
                           "padding": "14px 18px", "border": f"1px solid {var('border')}", "border_radius": "12px",
                           "background": var("surface"), "text_decoration": "none",
                           "transition": "border-color 120ms, background 120ms"},
    "& .docs-pager-card:hover": {"border_color": var("accent"), "background": var("sunk")},
    "& .docs-pager-next": {"align_items": "flex-end", "text_align": "right"},
    "& .docs-pager-label": {"font_size": "12px", "color": var("muted")},
    "& .docs-pager-title": {"display": "flex", "align_items": "center", "gap": "6px", "font_size": "15px",
                            "font_weight": "600", "color": var("link")},
    # landing
    "& .docs-hero p": {"max_width": "720px"},
    "& .docs-hero": {"padding": "16px 0 32px", "margin_bottom": "8px", "border_bottom": f"1px solid {var('border')}"},
    "& .docs-hero-eyebrow": {"font_size": "13px", "font_weight": "600", "color": var("link"), "margin": "0 0 8px"},
    "& .docs-hero .docs-lede": {"margin_bottom": "28px"},
    "& .docs-section-card": {"display": "flex", "flex_direction": "column", "gap": "6px", "height": "100%",
                             "box_sizing": "border-box", "padding": "18px 20px", "border": f"1px solid {var('border')}",
                             "border_radius": "12px", "background": var("surface"),
                             "text_decoration": "none", "color": "inherit",
                             "transition": "border-color 120ms, background 120ms"},
    "& .docs-section-card:hover": {"border_color": var("accent"), "background": var("sunk")},
    "& .docs-section-card-title": {"display": "flex", "align_items": "center", "gap": "10px",
                                   "font_size": "16px", "font_weight": "600", "color": var("ink")},
    "& .docs-section-card-icon, & .docs-section-card-num": {
        "display": "inline-flex", "align_items": "center", "justify_content": "center", "width": "28px",
        "height": "28px", "border_radius": "8px", "background": var("pill-bg"), "color": var("pill-ink"),
        "flex": "none"},
    "& .docs-section-card-num": {"border_radius": "999px", "font_size": "14px", "font_weight": "700",
                                 "font_variant_numeric": "tabular-nums"},
    "& .docs-section-card-story": {"font_size": "14px", "line_height": "1.55", "color": var("muted")},
    "& .docs-section-card-count": {"display": "flex", "align_items": "center", "gap": "6px", "margin_top": "auto",
                                   "padding_top": "8px", "font_size": "12px", "color": var("muted")},
    "& .docs-section-card-ref": {"background": var("sunk")},
    "& .docs-journey": {"margin_top": "8px", "padding_top": "8px"},
    "& .docs-journey-title.rt-Heading": {"margin": "16px 0 0", "font_size": "24px", "line_height": "1.3",
                                         "font_weight": "650", "letter_spacing": "-0.015em", "color": var("ink")},
    "& .docs-journey-lede": {"margin": "6px 0 0", "font_size": "15px", "line_height": "1.6", "color": var("muted")},
    "@media (min-width: 640px)": {
        "& .docs-product-sub": {"display": "inline"},
        "& .docs-kbd": {"display": "inline"},
        "& .docs-demo-as": {"display": "inline"},
        "& .docs-intro-icon": {"display": "inline-flex"},
        "& .docs-pager": {"grid_template_columns": "1fr 1fr"},
        "& .docs-main": {"padding": "32px 32px 72px"},
        "& .docs-title.rt-Heading": {"font_size": "40px"},
    },
    f"@media (min-width: {SIDEBAR_FROM})": {
        "& .docs-grid": {"grid_template_columns": "var(--docs-sidebar-w) minmax(0, 1fr)"},
        "& .rfds-header-menu-slot": {"display": "none"},
        "& .docs-tabs": {"display": "flex"},
        "& .docs-sidebar": {"position": "sticky", "top": HEADER, "align_self": "start", "width": "auto",
                            "height": f"calc(100dvh - {HEADER})", "visibility": "visible", "transform": "none",
                            "box_shadow": "none", "z_index": "auto", "padding": "24px 16px 40px",
                            "transition": "none"},
        "& .docs-main": {"padding": "36px 56px 72px"},
    },
    f"@media (min-width: {TOC_FROM})": {
        "& .docs-grid": {"grid_template_columns": "var(--docs-sidebar-w) minmax(0, 1fr) var(--docs-toc-w)"},
        "& .docs-grid > .docs-main:last-child": {"grid_column": "span 2"},
        "& .docs-toc": {"display": "block", "position": "sticky", "top": HEADER, "align_self": "start",
                        "max_height": f"calc(100dvh - {HEADER})", "overflow_y": "auto",
                        "box_sizing": "border-box", "padding": "40px 24px 40px 8px"},
    },
}


def _hotkeys() -> rx.Component:
    """Ctrl/Cmd+K puts the cursor in the search box (client-side, RFDS's hotkeys)."""
    from rfds.a11y.hotkeys import Hotkey, hotkeys

    from docs_site.search import INPUT_ID

    return hotkeys(Hotkey(combo=None, action_id="palette.open", label="Search the docs", group="Global",
                          event=rx.set_focus(INPUT_ID)))


def shell(slug: str, body: rx.Component, *, aside: rx.Component | None = None, wide: bool = False) -> rx.Component:
    """The frame around ``body``; ``aside`` is the "On this page" column, ``wide`` lets a page
    without one (the landing page) use that column's room too."""
    toc_column = rx.el.aside(aside, class_name="docs-toc") if aside is not None else rx.fragment()
    return rx.el.div(
        _header(slug),
        rx.el.div(
            _sidebar(slug),
            rx.el.main(rx.el.article(body, class_name="docs-article" + (" docs-article-wide" if wide else "")),
                       id="main", class_name="docs-main"),
            toc_column,
            class_name="docs-grid",
        ),
        _hotkeys(),
        class_name="rfds-shell docs-shell",
        custom_attrs={"data-rfds-shell": "root", "data-rfds-has-nav": "true"},
        style=_STYLE,
    )


def _content_page(slug: str):
    def page() -> rx.Component:
        doc = load(slug)
        section = nav.section_of(slug)
        body = rx.box(
            rx.el.div(
                # the first crumb is the chapter: "Chapter 2 · Show your data", to its first page
                rfds.breadcrumbs({"label": chapter_label(section), "href": route_of(nav.first(section))},
                                 {"label": doc.title}, truncate_chars=48),
                _page_actions(slug),
                class_name="docs-article-head",
            ),
            rx.heading(doc.title, size="8", as_="h1", class_name="docs-title"),
            rx.text(doc.description, as_="p", class_name="docs-lede") if doc.description else rx.fragment(),
            _api_tags(slug),
            _meta(slug),
            _chapter_intro(slug),
            render_page(doc),
            _prev_next(slug),
        )
        return shell(slug, body, aside=toc(doc))
    page.__name__ = page.__qualname__ = "page_" + slug.replace("/", "_").replace("-", "_")
    return page


def _section_card(section: nav.Section) -> rx.Component:
    """A chapter on the landing journey: its number, title, part of the story and page count, to
    its first page. The API reference's card has its icon where a chapter has its number."""
    count = len(nav.pages_of(section))
    n = nav.number(section)
    mark = (rx.el.span(str(n), class_name="docs-section-card-num") if n is not None else
            rx.el.span(rfds.icon(SECTION_ICONS.get(section.slug, "file_text"), size="sm"),
                       class_name="docs-section-card-icon"))
    return rx.link(
        rx.el.span(rx.el.span(mark, custom_attrs={"aria-hidden": "true"}), section.title,
                   class_name="docs-section-card-title"),
        rx.el.span(section.story, class_name="docs-section-card-story"),
        rx.el.span(f"{count} page{'s' if count != 1 else ''}", rfds.icon("arrow_right", size="sm"),
                   class_name="docs-section-card-count"),
        href=route_of(nav.first(section)), underline="none",
        class_name="docs-section-card" + ("" if n is not None else " docs-section-card-ref"),
        custom_attrs={"aria-label": f"{chapter_label(section).replace(' · ', ': ')}. {section.story}"},
    )


def _landing() -> rx.Component:
    doc = load("index")
    hero = rx.box(
        rx.el.p("Documentation", class_name="docs-hero-eyebrow"),
        rx.heading(doc.title, size="8", as_="h1", class_name="docs-title"),
        rx.text(doc.description, as_="p", class_name="docs-lede"),
        render_page(doc),
        class_name="docs-hero",
    )
    journey = rx.el.section(
        rx.heading("The guide, chapter by chapter", as_="h2", size="6", class_name="docs-journey-title",
                   id="the-guide"),
        rx.el.p("Each chapter adds to one dashboard for a fund team, from its first page to production.",
                class_name="docs-journey-lede"),
        rx.grid(*[_section_card(s) for s in nav.NAV], columns=rx.breakpoints(initial="1", sm="2"),
                spacing="4", margin_top="20px"),
        class_name="docs-journey", custom_attrs={"aria-labelledby": "the-guide"},
    )
    return shell("index", rx.box(hero, journey), wide=True)


def add_pages(app: rx.App) -> None:
    app.add_page(_landing, route="/", title=load("index").title, description=load("index").description)
    for slug in nav.ordered():
        doc = load(slug)
        app.add_page(_content_page(slug), route=route_of(slug), title=f"{doc.title} · lex-app dashboards",
                     description=doc.description)
