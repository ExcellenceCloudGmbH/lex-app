"""reflex-docgen's blocks as components in the site's look (RFDS components, the site's tokens)."""

import reflex as rx
import rfds
from reflex_components_code.code import Theme
from reflex_docgen.markdown import (
    CodeBlock, DirectiveBlock, Document, HeadingBlock, ListBlock, QuoteBlock, TableBlock, TextBlock,
    ThematicBreakBlock,
)
from reflex_docgen.markdown.transformer import DocumentTransformer

from docs_site.content import Page, plain, unique_anchor
from docs_site.theme import CODE_THEME

_HEADING_SIZE = {1: "7", 2: "6", 3: "4", 4: "3"}
#: room above a heading, on top of the 16px between blocks: a heading starts a new part of the page
_HEADING_SPACE = {1: "24px", 2: "32px", 3: "16px", 4: "4px"}
_TONE = {"info": "info", "note": "info", "success": "success", "tip": "success", "warning": "warning",
         "danger": "danger", "error": "danger"}
#: a callout's icon, and the name it gives the callout for a screen reader
_TONE_ICON = {"info": ("info", "Note"), "success": ("circle_check", "Tip"),
              "warning": ("triangle_alert", "Warning"), "danger": ("octagon_alert", "Danger")}
#: rx.code_block accepts no "text"-like language, so plain text is highlighted as "log"
_PLAIN = (None, "", "text", "txt", "plaintext")
_CODE_THEME = rx.Var.create(CODE_THEME).to(Theme)


def fence_title(flags: tuple[str, ...]) -> str | None:
    """A fence's file name: ```python title=funds/state.py```."""
    for flag in flags:
        if flag.startswith(("title=", "file=")):
            return flag.split("=", 1)[1].strip("\"'") or None
    return None


def highlighted(code: str, language: str | None) -> rx.Component:
    """The code alone, highlighted in the site's colours (theme.CODE_THEME), on no background of its own."""
    return rx.code_block(code, language="log" if language in _PLAIN else language, theme=_CODE_THEME,
                         show_line_numbers=False, can_copy=False,
                         code_tag_props={"style": {"fontFamily": "var(--docs-mono)"}})


def code_figure(code: str, language: str | None, title: str | None = None) -> rx.Component:
    """A code block: a header strip with an icon and the file name (or the language) and a copy
    button, over the code."""
    text = code.rstrip("\n")
    label = title or (language if language not in _PLAIN else "text")
    head = rx.el.figcaption(
        rx.el.span(rfds.icon("file_text" if title else "code", size="sm"), label, class_name="docs-code-label"),
        rfds.icon_button("copy", label="Copy code", tooltip=False, size="sm", on_click=rx.set_clipboard(text),
                         class_name="docs-code-copy"),
        class_name="docs-code-head",
    )
    return rx.el.figure(head, highlighted(text, language), class_name="docs-code")


def callout(content: rx.Component, tone: str) -> rx.Component:
    """A ```md alert``` block: a tinted panel with the tone's icon (info, success, warning, danger)."""
    icon, name = _TONE_ICON[tone]
    return rx.el.div(
        rx.el.span(rfds.icon(icon, size="md", label=name), class_name="docs-callout-icon"),
        rx.el.div(content, class_name="docs-callout-body"),
        class_name=f"docs-callout docs-callout-{tone}", custom_attrs={"role": "note"},
    )


class LexDocTransformer(DocumentTransformer[rx.Component]):
    def __init__(self, page: Page, live: bool = True):
        """live=False is for text this site did not write (docstrings): no code runs and no
        directive raises; python fences and md directives show as plain code."""
        self.page = page
        self.live = live
        if live:
            from docs_site.render.live import PageModule

            self.module = PageModule(page.slug)
        self._demos = 0
        self._anchors: set[str] = set()     # this page's heading ids so far (content.unique_anchor)

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
                          size=_HEADING_SIZE.get(block.level, "3"), id=unique_anchor(text, self._anchors),
                          margin_top=_HEADING_SPACE.get(block.level, "0"), letter_spacing="-0.01em",
                          scroll_margin_top="72px")

    def text_block(self, block: TextBlock) -> rx.Component:
        return rx.text(*self.transform_spans(block.children), as_="p", size="3", line_height="1.7")

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
        kinds = [f for f in block.flags if "=" not in f]
        if self.live and block.language == "python" and kinds:
            return self.live_block(block, kinds[0])
        return code_figure(block.content, block.language, fence_title(block.flags))

    def live_block(self, block: CodeBlock, kind: str) -> rx.Component:
        from docs_site.render.live import demo_card, to_component

        if kind == "exec":
            self.module.run(block.content)
            return rx.fragment()
        if kind in ("demo", "demo-only"):
            self._demos += 1
            shown = to_component(self.module.value(block.content), f"{self.page.slug}#{self._demos}")
            return demo_card(None if kind == "demo-only" else block.content, shown,
                             title=fence_title(block.flags))
        raise ValueError(f"{self.page.slug}: ```python {kind}``` is not a fence this site knows "
                         "(exec, demo, demo-only)")

    def directive(self, block: DirectiveBlock) -> rx.Component:
        if not self.live:
            return code_figure(block.content, "markdown")
        if block.name == "alert":
            tone = _TONE.get(block.args[0] if block.args else "info", "info")
            return callout(rx.fragment(*self.transform_blocks(block.children)), tone)
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
        return rx.el.code(span.code, class_name="docs-code-span")

    def link(self, span) -> rx.Component:
        return rx.link(*self.transform_spans(span.children), href=span.target,
                       is_external=span.target.startswith("http"))

    def image(self, span) -> rx.Component:
        return rx.image(src=span.src, alt=plain(span.children))

    def line_break(self, span) -> rx.Component:
        return rx.fragment(" ") if span.soft else rx.el.br()
