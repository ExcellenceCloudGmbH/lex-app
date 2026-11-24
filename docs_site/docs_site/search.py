"""Search over every page and section, in the app's backend, as the reader types."""

from dataclasses import dataclass
from functools import cache

import reflex as rx
from reflex_docgen.markdown import HeadingBlock, TextBlock

from docs_site.content import all_slugs, anchor, load, plain


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
        heading, words = "", []
        for block in page.document.blocks:
            if isinstance(block, HeadingBlock) and block.level in (2, 3):
                out.append(Entry(slug, page.title, heading, anchor(heading) if heading else "", " ".join(words)))
                heading, words = plain(block.children), []
            elif isinstance(block, TextBlock):
                words.append(plain(block.children))
        out.append(Entry(slug, page.title, heading, anchor(heading) if heading else "", " ".join(words)))
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

    @rx.var
    def results(self) -> list[dict]:
        from docs_site.layout import route_of

        return [{"title": e.title, "heading": e.heading,
                 "href": route_of(e.slug) + (f"#{e.anchor}" if e.anchor else "")}
                for e in find(build_index(), self.query)]


def box() -> rx.Component:
    return rx.popover.root(
        rx.popover.trigger(rx.input(placeholder="Search the docs", value=SearchState.query,
                                    on_change=SearchState.set_query,
                                    width=rx.breakpoints(initial="150px", sm="260px"),
                                    aria_label="Search")),
        rx.popover.content(
            rx.foreach(SearchState.results, lambda r: rx.link(
                rx.vstack(rx.text(r["title"], weight="medium", size="2"),
                          rx.text(r["heading"], size="1", color_scheme="gray"), spacing="0"),
                href=r["href"], underline="none")),
            width="320px", max_width="90vw",
        ),
        open=SearchState.results.length() > 0,
    )
