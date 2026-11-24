"""The Markdown pages under content/, parsed once by reflex-docgen."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from reflex_docgen.markdown import (
    DirectiveBlock, Document, HeadingBlock, ListBlock, QuoteBlock, parse_document,
)

from docs_site import reference

CONTENT = Path(__file__).resolve().parents[1] / "content"


def anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def unique_anchor(text: str, used: set[str]) -> str:
    """``anchor(text)``, with "-2", "-3"... when the page already has that id; ``used`` is the
    page's ids so far, and gets this one."""
    base = out = anchor(text)
    n = 1
    while out in used:
        n += 1
        out = f"{base}-{n}"
    used.add(out)
    return out


def rendered_headings(blocks, live: bool = True):
    """The headings the page renders as headings, in the order it renders them (as
    ``render.blocks`` does): inside lists, quotes and directives too, except a ``tabs``
    directive's ``##`` lines, which are tab titles. Off-live pages show directives as code."""
    for block in blocks:
        if isinstance(block, HeadingBlock):
            yield block
        elif isinstance(block, ListBlock):
            for item in block.items:
                yield from rendered_headings(item.children, live)
        elif isinstance(block, QuoteBlock):
            yield from rendered_headings(block.children, live)
        elif isinstance(block, DirectiveBlock) and live:
            children = block.children
            if block.name == "tabs":
                children = [c for c in children if not (isinstance(c, HeadingBlock) and c.level == 2)]
            yield from rendered_headings(children, live)


def plain(spans) -> str:
    """A heading's or paragraph's text, without its markup."""
    out = []
    for span in spans:
        if hasattr(span, "text"):
            out.append(span.text)
        elif hasattr(span, "code"):
            out.append(span.code)
        elif hasattr(span, "children"):
            out.append(plain(span.children))
    return "".join(out)


@dataclass(frozen=True)
class Page:
    slug: str
    title: str
    description: str
    source: str
    document: Document
    headings: list[tuple[int, str, str]]


def all_slugs() -> list[str]:
    files = sorted(str(p.relative_to(CONTENT).with_suffix("")) for p in CONTENT.rglob("*.md"))
    return files + [f"reference/{group}" for group in reference.GROUPS]


def headings_of(document: Document, live: bool = True) -> list[tuple[int, str, str]]:
    """The page's ``##``/``###`` headings (the "On this page" list): level, text and the id the
    rendered heading carries, de-duplicated over every heading the page renders."""
    top = {id(b) for b in document.blocks}
    used: set[str] = set()
    out = []
    for block in rendered_headings(document.blocks, live):
        text = plain(block.children)
        anchor_id = unique_anchor(text, used)
        if id(block) in top and block.level in (2, 3):
            out.append((block.level, text, anchor_id))
    return out


@cache
def load(slug: str) -> Page:
    if slug.startswith("reference/"):
        source = reference.page_source(slug.split("/", 1)[1])
    else:
        source = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
    document = parse_document(source)
    meta = document.metadata or {}
    title = str(meta.get("title") or slug.rsplit("/", 1)[-1].replace("-", " ").capitalize())
    headings = headings_of(document, live=not slug.startswith("reference/"))
    return Page(slug=slug, title=title, description=str(meta.get("description") or ""),
                source=source, document=document, headings=headings)
