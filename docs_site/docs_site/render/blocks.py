"""reflex-docgen's blocks as components in lex-app's look (RFDS components and tokens)."""

import reflex as rx
import rfds
from reflex_docgen.markdown import (
    CodeBlock, DirectiveBlock, Document, HeadingBlock, ListBlock, QuoteBlock, TableBlock, TextBlock,
    ThematicBreakBlock,
)
from reflex_docgen.markdown.transformer import DocumentTransformer

from docs_site.content import Page, anchor, plain

_HEADING_SIZE = {1: "7", 2: "6", 3: "5", 4: "4"}
_TONE = {"info": "info", "success": "success", "warning": "warning", "danger": "danger", "error": "danger"}


class LexDocTransformer(DocumentTransformer[rx.Component]):
    def __init__(self, page: Page):
        self.page = page

    # -- the page ---------------------------------------------------------------
    def transform(self, document: Document) -> rx.Component:
        return rx.vstack(*self.transform_blocks(document.blocks), spacing="4", align="stretch",
                         class_name="docs-prose")

    # -- blocks -----------------------------------------------------------------
    def frontmatter(self, block) -> rx.Component:
        return rx.fragment()

    def heading(self, block: HeadingBlock) -> rx.Component:
        text = plain(block.children)
        return rx.heading(*self.transform_spans(block.children), as_=f"h{block.level}",
                          size=_HEADING_SIZE.get(block.level, "3"), id=anchor(text))

    def text_block(self, block: TextBlock) -> rx.Component:
        return rx.text(*self.transform_spans(block.children), as_="p")

    def list_block(self, block: ListBlock) -> rx.Component:
        items = [self.transform_list_item(item) for item in block.items]
        return rx.list.ordered(*items) if block.ordered else rx.list.unordered(*items)

    def transform_list_item(self, item) -> rx.Component:
        return rx.list.item(*self.transform_blocks(item.children))

    def quote(self, block: QuoteBlock) -> rx.Component:
        return rx.blockquote(*self.transform_blocks(block.children))

    def table(self, block: TableBlock) -> rx.Component:
        head = rx.table.row(*[rx.table.column_header_cell(*self.transform_spans(c.children))
                              for c in block.header.cells])
        body = [rx.table.row(*[rx.table.cell(*self.transform_spans(c.children)) for c in row.cells])
                for row in block.rows]
        return rx.table.root(rx.table.header(head), rx.table.body(*body), variant="surface")

    def thematic_break(self, block: ThematicBreakBlock) -> rx.Component:
        return rx.separator()

    def code_block(self, block: CodeBlock) -> rx.Component:
        if block.language == "python" and block.flags:
            return self.live_block(block)
        return rx.code_block(block.content.rstrip("\n"), language=block.language or "text",
                             show_line_numbers=False, can_copy=True)

    def live_block(self, block: CodeBlock) -> rx.Component:
        raise NotImplementedError("python demo/exec fences come with render/live.py")

    def directive(self, block: DirectiveBlock) -> rx.Component:
        if block.name == "alert":
            tone = _TONE.get(block.args[0] if block.args else "info", "info")
            return rfds.inline_message(rx.vstack(*self.transform_blocks(block.children), spacing="2"),
                                       tone=tone)
        if block.name == "tabs":
            return self._tabs(block)
        raise ValueError(f"{self.page.slug}: no ```md {block.name}``` directive on this site "
                         "(there are: alert, tabs)")

    def _tabs(self, block: DirectiveBlock) -> rx.Component:
        panes: list[tuple[str, list]] = []
        for child in block.children:
            if isinstance(child, HeadingBlock) and child.level == 2:
                panes.append((plain(child.children), []))
            elif panes:
                panes[-1][1].append(child)
        if not panes:
            raise ValueError(f"{self.page.slug}: a ```md tabs``` block needs a '## Title' per tab")
        return rx.tabs.root(
            rx.tabs.list(*[rx.tabs.trigger(title, value=str(i)) for i, (title, _) in enumerate(panes)]),
            *[rx.tabs.content(*self.transform_blocks(tuple(body)), value=str(i)) for i, (_, body) in enumerate(panes)],
            default_value="0",
        )

    # -- spans ------------------------------------------------------------------
    def text_span(self, span) -> rx.Component:
        return rx.fragment(span.text)

    def bold(self, span) -> rx.Component:
        return rx.el.strong(*self.transform_spans(span.children))

    def italic(self, span) -> rx.Component:
        return rx.text(*self.transform_spans(span.children), as_="em")

    def strikethrough(self, span) -> rx.Component:
        return rx.text(*self.transform_spans(span.children), as_="s")

    def code_span(self, span) -> rx.Component:
        return rx.code(span.code)

    def link(self, span) -> rx.Component:
        return rx.link(*self.transform_spans(span.children), href=span.target,
                       is_external=span.target.startswith("http"))

    def image(self, span) -> rx.Component:
        return rx.image(src=span.src, alt=plain(span.children))

    def line_break(self, span) -> rx.Component:
        return rx.fragment(" ") if span.soft else rx.el.br()
