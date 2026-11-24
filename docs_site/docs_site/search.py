"""Search over every page and section, in the app's backend, as the reader types."""

from dataclasses import dataclass
from functools import cache

import reflex as rx
import rfds
from reflex_docgen.markdown import HeadingBlock, TextBlock

from docs_site.content import all_slugs, load, plain


@dataclass(frozen=True)
class Entry:
    slug: str
    title: str
    heading: str
    anchor: str
    text: str


@cache
def build_index() -> list[Entry]:
    out: list[Entry] = []
    for slug in all_slugs():
        page = load(slug)
        anchors = iter(a for _, _, a in page.headings)     # the same headings, in order, de-duplicated
        heading, anchor_id, words = "", "", []
        for block in page.document.blocks:
            if isinstance(block, HeadingBlock) and block.level in (2, 3):
                out.append(Entry(slug, page.title, heading, anchor_id, " ".join(words)))
                heading, anchor_id, words = plain(block.children), next(anchors), []
            elif isinstance(block, TextBlock):
                words.append(plain(block.children))
        out.append(Entry(slug, page.title, heading, anchor_id, " ".join(words)))
    return out


def find(index: list[Entry], query: str, limit: int = 8) -> list[Entry]:
    terms = [t for t in query.lower().split() if t]
    if not terms:
        return []

    def rank(e: Entry) -> int | None:
        hay = f"{e.title} {e.heading} {e.text}".lower()
        if not all(t in hay for t in terms):
            return None
        if all(t in e.title.lower() for t in terms):
            return 0 if not e.heading else 1
        return 2 if all(t in e.heading.lower() for t in terms) else 3

    ranked = sorted((r, i, e) for i, e in enumerate(index) if (r := rank(e)) is not None)
    return [e for _, _, e in ranked[:limit]]


class SearchState(rx.State):
    query: str = ""

    @rx.event
    def set_query(self, value: str):
        self.query = value

    @rx.event
    def clear(self):
        self.query = ""

    @rx.event
    def key(self, key: str):
        if key == "Escape":
            self.query = ""

    @rx.var
    def results(self) -> list[dict]:
        from docs_site.layout import route_of

        return [{"title": e.title, "heading": e.heading,
                 "href": route_of(e.slug) + (f"#{e.anchor}" if e.anchor else "")}
                for e in find(build_index(), self.query)]


#: the search box's id: Ctrl/Cmd+K focuses it (layout._hotkeys)
INPUT_ID = "docs-search-input"


def box() -> rx.Component:
    return rx.box(
        rx.input(
            rx.input.slot(rfds.icon("search", size="sm"), side="left"),
            rx.input.slot(
                rx.cond(
                    SearchState.query != "",
                    rfds.icon_button("x", label="Clear search", size="sm", tooltip=False,
                                     on_click=SearchState.clear),
                    rx.el.kbd("Ctrl K", class_name="docs-kbd", custom_attrs={"aria-hidden": "true"}),
                ),
                side="right",
            ),
            placeholder="Search the docs", value=SearchState.query, on_change=SearchState.set_query,
            on_key_down=SearchState.key, width=rx.breakpoints(initial="132px", sm="220px", lg="280px"),
            aria_label="Search", id=INPUT_ID, custom_attrs={"aria-keyshortcuts": "Control+K Meta+K"},
        ),
        rx.cond(
            SearchState.results.length() > 0,
            rx.el.ul(
                rx.foreach(SearchState.results, lambda r: rx.el.li(rx.link(
                    rx.vstack(rx.text(r["title"], weight="medium", size="2"),
                              rx.text(r["heading"], size="1", color_scheme="gray"), spacing="0"),
                    href=r["href"], underline="none", on_click=SearchState.clear, display="block",
                    padding="6px 8px", border_radius="8px")),
                ),
                aria_label="Search results", class_name="docs-results",
                position="absolute", top="100%", right="0", z_index="50", width="320px", max_width="90vw",
                border_radius="12px", padding="8px", list_style="none", margin="6px 0 0 0",
                box_shadow="var(--shadow-4)",
            ),
            rx.fragment(),
        ),
        position="relative", class_name="docs-search",
    )
