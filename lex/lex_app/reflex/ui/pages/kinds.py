"""The page kinds: each takes named slots and decides where they sit."""

from __future__ import annotations

import dataclasses
from typing import Any, ClassVar

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


def _stacked(ctx: Build, parts: list) -> list:
    """The parts built in order; facts that follow one another become one key-value list."""
    import rfds

    built: list = []
    run: list = []

    def close() -> None:
        if run:
            built.append(rfds.key_value(*vocab.build_all(ctx, run), columns=min(3, len(run))))
            run.clear()

    for part in parts:
        if isinstance(part, vocab.Fact):
            run.append(part)
            continue
        close()
        built.append(part.build(ctx))
    close()
    return built


@dataclasses.dataclass(eq=False)
class Section(Part):
    nests: ClassVar[bool] = False

    title: str
    content: tuple = ()
    load: Any = None
    empty: Any = None
    visible: Any = None

    def body(self, ctx: Build) -> rx.Component:
        import rfds

        if any(isinstance(part, Section) for part in self.content):
            raise ctx.fail("a section inside a section.",
                           "Sections do not nest: give the page each of them, in `sections=[...]`.")
        parts = take(ctx, list(self.content), (*vocab.CONTENT, tables.Table),
                     "A section holds `ui.text`, `ui.table`, `ui.message`, `ui.link` and the other `ui.*` "
                     "components, or `ui.custom_block(..., reason=\"...\")`.")
        has_table = any(isinstance(part, tables.Table) for part in parts)
        if self.empty is not None:
            if not has_table:
                raise ctx.fail("`empty=` is the sentence for a table without rows, and this section has no table.",
                               "Remove `empty=`, or put a `ui.table(...)` in the section.")
            # A table without its own sentence takes the section's: a copy, never the author's part.
            parts = [dataclasses.replace(part, empty=self.empty)
                     if isinstance(part, tables.Table) and part.empty is None else part for part in parts]
        shape = "table" if has_table and len(parts) == 1 else "rect"
        return states(ctx, self.load, rfds.stack(*_stacked(ctx, parts)), shape)

    def _render(self, ctx: Build) -> rx.Component:
        return self.body(ctx)


def section(title: str, *content: Any, load: Any = None, empty: Any = None, visible: Any = None) -> Section:
    """A titled part of the page. With ``load=`` it loads on its own and shows its own skeleton and failure."""
    if not isinstance(title, str) or not title:
        raise TypeError(f"ui.section() takes its title first, a sentence; got {title!r}")
    return Section(title, tuple(content), load, empty, visible)

def _apart(button: rx.Component) -> rx.Component:
    """``button`` in a wrapper RFDS can tell from the primary action.

    RFDS 1.0.0 sorts a page's actions with ``action not in primary``, and Reflex components of one
    type compare equal, so a second button beside a primary one was dropped. A fragment is another
    type; RFDS reads the button's kind and intent from the wrapper.
    """
    from rfds.components.actions._common import get_meta, set_meta

    return set_meta(rx.fragment(button), **get_meta(button))


class Kind:
    """A filled page: what `@ui.page` and `@ui.dashboard` functions return."""

    title: Any
    load: Any = None
    actions: tuple = ()

    def loads(self) -> tuple[list, list]:
        """(the page's load, the loads of its parts)."""
        return ([self.load] if self.load is not None else []), []

    def build(self, ctx: Build) -> rx.Component:
        raise NotImplementedError

    def _buttons(self, ctx: Build) -> list:
        """The page's actions as RFDS places them: one primary button; one more as a button, more in a menu."""
        ctx = ctx.at("actions", "actions")
        parts = take(ctx, list(self.actions), (vocab.Button, vocab.Link),
                     "`actions=` holds `ui.button(...)` (or `ui.link(...)`, shown as a button).")
        first = parts[0] if parts and isinstance(parts[0], vocab.Button) and parts[0].tone != "danger" else None
        rest = [part for part in parts if part is not first]
        built: list = []
        if first is not None:
            first.primary = True
            built.append(first.build(ctx))
        if len(rest) == 1:
            built.append(_apart(rest[0].build(ctx)))
        else:
            built.extend(part.menu_entry(ctx) for part in rest)
        return built

    def _title(self, ctx: Build) -> Any:
        if self.title is None or (isinstance(self.title, str) and not self.title):
            raise ctx.fail("the page has no title.", "Give the kind `title=\"...\"`, or a state value.")
        return say(self.title)


@dataclasses.dataclass(eq=False)
class Record(Kind):
    title: Any
    load: Any = None
    status: Any = None
    facts: tuple = ()
    sections: tuple = ()
    tabs: bool = False
    actions: tuple = ()

    def loads(self) -> tuple[list, list]:
        return ([self.load] if self.load is not None else []), [s.load for s in self.sections if s.load is not None]

    def build(self, ctx: Build) -> rx.Component:
        import rfds
        from rfds import templates

        head: list = []
        if self.status is not None:
            (badge,) = take(ctx.at("status"), self.status, (vocab.Status,), "`status=` holds one `ui.status(word, tone=)`.")
            head.append(badge.build(ctx.at("status")))
        facts = take(ctx.at("facts"), list(self.facts), (vocab.Fact,),
                     "Use `ui.fact(label, value)`.")
        if facts:
            head.append(rfds.key_value(*vocab.build_all(ctx.at("facts"), facts), columns=min(3, len(facts))))
        parts = take(ctx.at("sections"), list(self.sections), (Section,), "`sections=` holds `ui.section(title, ...)`.")
        if not parts:
            raise ctx.at("sections").fail("a record page needs at least one section.",
                                          "Add `sections=[ui.section(\"Details\", ...)]`.")
        built = [templates.ObjectSection(id=slug(part.title), title_id=part.title, title_default=part.title,
                                         content=part.build(ctx.at(f"sections[{index}]")))
                 for index, part in enumerate(parts)]
        page = templates.object_page(
            title=self._title(ctx), path=ctx.route or "/", header=rfds.stack(*head) if head else rx.fragment(),
            sections=built, navigation="tabs" if self.tabs and len(built) > 1 else "anchor",
            actions=self._buttons(ctx))
        return states(ctx, self.load, page, "rect")


def record(*, title: Any, load: Any = None, status: Any = None, facts: Any = (), sections: Any = (),
           tabs: bool = False, actions: Any = ()) -> Record:
    """One thing: its key facts, then its parts."""
    return Record(title, load, status, tuple(facts), tuple(sections), bool(tabs), tuple(actions))


@dataclasses.dataclass(eq=False)
class List(Kind):
    title: Any
    load: Any = None
    table: Any = None
    actions: tuple = ()

    def build(self, ctx: Build) -> rx.Component:
        from rfds import templates

        if not isinstance(self.table, tables.Table):
            raise ctx.at("table").fail(f"got {type(self.table).__name__}.", "`table=` holds one `ui.table(Model, columns=[...])`.")
        page = templates.list_report(title=self._title(ctx), path=ctx.route or "/",
                                     table=self.table.build(ctx.at("table")), actions=self._buttons(ctx))
        return states(ctx, self.load, page, "table")


def list_(*, title: Any, load: Any = None, table: Any = None, actions: Any = ()) -> List:
    """Finding things: a table to search, sort and open from."""
    return List(title, load, table, tuple(actions))
