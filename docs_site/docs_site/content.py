"""The Markdown pages under content/, parsed once by reflex-docgen."""

import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from reflex_docgen.markdown import Document, HeadingBlock, parse_document

CONTENT = Path(__file__).resolve().parents[1] / "content"


def anchor(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


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
    return sorted(str(p.relative_to(CONTENT).with_suffix("")) for p in CONTENT.rglob("*.md"))


@cache
def load(slug: str) -> Page:
    source = (CONTENT / f"{slug}.md").read_text(encoding="utf-8")
    document = parse_document(source)
    meta = document.metadata or {}
    title = str(meta.get("title") or slug.rsplit("/", 1)[-1].replace("-", " ").capitalize())
    headings = [(b.level, plain(b.children), anchor(plain(b.children)))
                for b in document.blocks if isinstance(b, HeadingBlock) and b.level in (2, 3)]
    return Page(slug=slug, title=title, description=str(meta.get("description") or ""),
                source=source, document=document, headings=headings)
