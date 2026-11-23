"""The page kinds: each takes named slots and decides where they sit."""

from __future__ import annotations

import dataclasses
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui import runs
from lex.lex_app.reflex.ui.pages import tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, Part, say, slug, take


def states(ctx: Build, load: Any, content: rx.Component, shape: str) -> rx.Component:
    """``content`` once ``load`` has finished; before that its skeleton, or its failure in place."""
    import rfds

    if load is None:
        return content
    problem = runs.failed(load)
    waiting = rx.cond(problem != "", rfds.inline_message(problem, tone="danger"),
                      rfds.skeleton(loading=True, variant=shape))
    whole = rx.cond(runs.loaded(load), content, waiting)
    return rx.box(whole, on_mount=load, width="100%") if ctx.mounts else whole


@dataclasses.dataclass(eq=False)
class Section(Part):
    title: str
    content: tuple = ()
    load: Any = None
    empty: Any = None
    visible: Any = None

    def body(self, ctx: Build) -> rx.Component:
        import rfds

        parts = take(ctx, list(self.content), (*vocab.CONTENT, tables.Table),
                     "A section holds `ui.text`, `ui.table`, `ui.message`, `ui.link` and the other `ui.*` "
                     "components, or `ui.custom_block(..., reason=\"...\")`.")
        if self.empty is not None:              # a table without its own sentence takes the section's; a copy, not the part
            parts = [dataclasses.replace(part, empty=self.empty)
                     if isinstance(part, tables.Table) and part.empty is None else part for part in parts]
        return states(ctx, self.load, rfds.stack(*vocab.build_all(ctx, parts)), "rect")

    def _render(self, ctx: Build) -> rx.Component:
        return self.body(ctx)


def section(title: str, *content: Any, load: Any = None, empty: Any = None, visible: Any = None) -> Section:
    """A titled part of the page. With ``load=`` it loads on its own and shows its own skeleton and failure."""
    if not isinstance(title, str) or not title:
        raise TypeError(f"ui.section() takes its title first, a sentence; got {title!r}")
    return Section(title, tuple(content), load, empty, visible)
