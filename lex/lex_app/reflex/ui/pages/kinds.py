"""The page kinds: each takes named slots and decides where they sit."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, Callable, ClassVar

import reflex as rx

from lex.lex_app.reflex.ui import runs
from lex.lex_app.reflex.ui.pages import tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, NotASentence, Part, called, say, slug, take, with_overlays


def _many(value: Any) -> tuple:
    """A list slot as given: a list, one part, or nothing. The kind checks what is in it."""
    return () if value is None else tuple(value) if isinstance(value, (list, tuple)) else (value,)


def states(ctx: Build, load: Any, content: rx.Component, shape: str) -> rx.Component:
    """``content`` once ``load`` has finished; before that its skeleton, or its failure in place."""
    import rfds

    # The page waits as a whole: keeping its header while the body loads would show an empty
    # title and actions that can be clicked before the data they act on has arrived.
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

    title: Any
    content: tuple = ()
    load: Any = None
    empty: Any = None
    visible: Any = None
    collapsed: bool = False
    actions: tuple = ()

    def body(self, ctx: Build) -> rx.Component:
        return self._content(ctx)[0]

    def fixed(self, ctx: Build, where: str) -> None:
        """Refuse what a section ``where`` (a record page's, a tab) cannot have: a title from the state, actions."""
        if not isinstance(self.title, str):
            raise ctx.fail(f"a section's title is a state value, and {where} names its sections with fixed words.",
                           "Give the section a sentence as its title; put the value inside it, as `ui.text(...)` "
                           "or `ui.fact(...)`.")
        if self.actions:
            raise ctx.fail(f"the section `{self.title}` has `actions=`, and {where} has no heading of its own "
                           "to hold them.",
                           "Put the buttons inside the section, or give the page `actions=[...]`.")

    def titled(self, ctx: Build) -> rx.Component:
        """The section with its heading: RFDS's section, closed at first when ``collapsed``.

        A closed section mounts what it holds when the user first opens it (a chart then fits the
        space it is shown in), except a table, which reads its rows at once for the count in the
        title. Its actions sit in RFDS's section header; a closed section shows them when opened
        (RFDS never puts a control inside the disclosure's summary).
        """
        import rfds
        from rfds.components.inputs._shared import client_state

        opened = client_state("lexOpened", False) if self.collapsed else None
        content, parts = self._content(ctx, opened)
        title: Any = say(self.title)
        if self.collapsed:
            title = self._counted(title, parts)
        extra: dict[str, Any] = {}
        if self.actions:
            at = ctx.at("actions")
            buttons = take(at, list(self.actions), (vocab.Button, vocab.Link),
                           "A section's `actions=` holds `ui.button(...)` and `ui.link(...)`.")
            extra["actions"] = rfds.cluster(*(part.build(at) for part in buttons), gap="2")
        if opened is not None:
            # A click on the closed section can only be on its summary: the content mounts from then on.
            extra["on_click"] = opened.set_value(True)
        return rfds.section(content, title=title, collapsible=self.collapsed, default_open=not self.collapsed,
                            **extra)

    def _counted(self, title: Any, parts: list) -> Any:
        """A closed section's title, with its table's row count once the table has read its rows."""
        from lex.lex_app.reflex.ui.pages import rows_table

        held = [part for part in parts if isinstance(part, tables.Table)]
        state = rows_table._CACHE_BY_TABLE.get(held[0]) if len(held) == 1 else None
        if state is None:
            return title
        return rx.cond(state.loaded_at != "", say("{title} ({count, number})", title=title, count=state.total_rows),
                       title)

    def _content(self, ctx: Build, opened: Any = None) -> tuple[rx.Component, list]:
        """The section's content, and its parts as built (a table may be a copy carrying ``empty=``).

        ``opened``: a client state, true once the user opened the closed section; every part but a
        table mounts only then.
        """
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
        ctx = dataclasses.replace(ctx, caption=self.title if isinstance(self.title, str) else "",
                                  under_load=ctx.under_load or self.load is not None)
        if opened is None:
            return states(ctx, self.load, rfds.stack(*_stacked(ctx, parts)), shape), parts
        built: list = []
        group: list = []

        def close() -> None:
            if group:
                built.append(rx.cond(opened.value, rfds.stack(*_stacked(ctx, list(group)))))
                group.clear()

        for part in parts:
            if isinstance(part, tables.Table):
                close()
                built.append(part.build(ctx))
            else:
                group.append(part)
        close()
        return states(ctx, self.load, rfds.stack(*built), shape), parts

    def _render(self, ctx: Build) -> rx.Component:
        return self.titled(ctx) if self.collapsed or self.actions else self.body(ctx)


def section(title: Any, *content: Any, load: Any = None, empty: Any = None, visible: Any = None,
            collapsed: bool = False, actions: Any = ()) -> Section:
    """A titled part of the page. With ``load=`` it loads on its own and shows its own skeleton and failure.

    ``title`` is a sentence, or a state value (``ImportState.lines_title``, "Lines (12)") where the
    section has a heading of its own -- not on a record page or a tab, which name their sections
    with fixed words. ``actions=[ui.button(...)]`` puts buttons in the section's heading.

    ``collapsed=True`` closes it until the user opens it (in the browser, no round trip); what it
    holds is mounted when the user first opens it, so a chart fits the space it is shown in. A
    closed section that holds a table reads the rows at once and says in its title how many there
    are, "Fields read (12)". A record page's sections are always open.
    """
    if isinstance(title, str) and not title or not isinstance(title, (str, rx.Var)):
        raise TypeError(f"ui.section() takes its title first, a sentence or a state value; got {title!r}")
    if not isinstance(collapsed, bool):
        raise TypeError(f"ui.section(collapsed=) takes True or False; got {collapsed!r}. `collapsed=True` closes "
                        "the section until the user opens it.")
    return Section(title, tuple(content), load, empty, visible, collapsed, _many(actions))


#: An action RFDS treats as a Save: it wants a "last saved" time beside it, which a page header cannot give.
_SAVE = re.compile(r"\b(save|speichern|sichern)\b", re.IGNORECASE)


def _apart(button: rx.Component) -> rx.Component:
    """``button`` in a wrapper RFDS can tell from the primary action.

    RFDS 1.0.0 sorts a page's actions with ``action not in primary``, and Reflex components of one
    type compare equal, so a second button beside a primary one was dropped. A fragment is another
    type; RFDS reads the button's kind and intent from the wrapper.
    """
    return vocab._marked(button, rx.fragment(button))


def _rfds(ctx: Build, build: Callable[[], Any]) -> Any:
    """Call one of RFDS's floorplans; a limit of its own becomes a ``PageError`` naming the page.

    ``build`` calls RFDS and nothing else: whatever it raises is reported as RFDS's refusal.
    """
    try:
        return build()
    except (ValueError, TypeError) as wrong:
        raise ctx.fail(f"RFDS's page layout refuses this page: {wrong}",
                       "The sentence above is RFDS's own: it names the limit the page goes over.") from wrong


def _section_ids(titles: list) -> list:
    """An id for each section: an anchor in the page's address, so letter-led and unlike the others."""
    ids: list = []
    for index, title in enumerate(titles):
        base = slug(title)
        if base == "part":
            base = f"section-{index + 1}"
        elif not base[0].isalpha():
            base = f"section-{base}"
        candidate, number = base, 2
        while candidate in ids:
            candidate, number = f"{base}-{number}", number + 1
        ids.append(candidate)
    return ids


class Kind:
    """A filled page: what `@ui.page` and `@ui.dashboard` functions return."""

    title: Any
    load: Any = None
    actions: tuple = ()

    def loads(self) -> tuple[list, list]:
        """(the page's load, the loads of its parts)."""
        return ([self.load] if self.load is not None else []), []

    def build(self, ctx: Build) -> rx.Component:
        """The page, and beside it the dialogs its buttons open."""
        return with_overlays(ctx, self._page(ctx))

    def _page(self, ctx: Build) -> rx.Component:
        raise NotImplementedError

    def _buttons(self, ctx: Build, lead: bool = True) -> list:
        """The page's actions as RFDS places them: one primary button; one more as a button, more in a menu.

        ``lead=False`` when the page has its primary action elsewhere (a form's submit button).
        """
        ctx = ctx.at("actions", "actions")
        parts = take(ctx, list(self.actions), (vocab.Button, vocab.Link),
                     "`actions=` holds `ui.button(...)` (or `ui.link(...)`, shown as a button).")
        for part in parts:
            if isinstance(part.label, str) and _SAVE.search(part.label):
                raise ctx.fail(f"an action is called `{part.label}`.",
                               "RFDS shows a last-saved time beside a Save action, which a page header cannot give "
                               "yet. Name the action for what it does (`\"Apply changes\"`); saving belongs to a form.")
        first = (parts[0] if lead and parts and isinstance(parts[0], vocab.Button) and parts[0].tone != "danger"
                 else None)
        rest = [part for part in parts if part is not first]
        built: list = []
        if first is not None:
            built.append(dataclasses.replace(first, primary=True).build(ctx))     # a copy: the part is the author's
        if len(rest) == 1:
            built.append(_apart(rest[0].build(ctx)))
        else:
            built.extend(part.menu_entry(ctx) for part in rest)
        return built

    def _title(self, ctx: Build) -> Any:
        title = self.title
        if title is None or (isinstance(title, str) and not title.strip()):
            raise ctx.fail("the page has no title.", "Give the kind `title=\"...\"`, or a state value.")
        try:
            return say(title)
        except NotASentence:
            raise ctx.fail(f"the page's title is {called(title)}.",
                           "Give the kind `title=\"...\"`, or a state value.") from None


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
        parts = [s for s in self.sections if isinstance(s, Section)]     # anything else is refused when built
        return ([self.load] if self.load is not None else []), [s.load for s in parts if s.load is not None]

    def _sections(self, ctx: Build) -> list:
        """The sections as RFDS's object page takes them, each checked first."""
        from rfds import templates

        from lex.lex_app.reflex.access import permission
        from lex.lex_app.reflex.ui import guard

        parts = take(ctx.at("sections"), list(self.sections), (Section,), "`sections=` holds `ui.section(title, ...)`.")
        if not parts:
            raise ctx.at("sections").fail("a record page needs at least one section.",
                                          "Add `sections=[ui.section(\"Details\", ...)]`.")
        for index, part in enumerate(parts):
            part.fixed(ctx.at(f"sections[{index}]"), "a record page")
        titles = [part.title for part in parts]
        twice = sorted({title for title in titles if titles.count(title) > 1})
        if twice:
            raise ctx.at("sections").fail(f"two sections are both called `{twice[0]}`.",
                                          "Give each section its own title.")
        built: list = []
        for index, (part, ident) in enumerate(zip(parts, _section_ids(titles))):
            at = ctx.at(f"sections[{index}]")
            if part.collapsed:
                raise at.fail(f"the section `{part.title}` is collapsed, and a record page's sections are always open.",
                              "RFDS lists them under \"On this page\". To show one section at a time, give the "
                              "record `tabs=True`.")
            needs = None
            if isinstance(part.visible, guard.Requirement):
                needs = permission(part.visible.target, part.visible.scope)     # RFDS keeps the heading and says why
            elif not (part.visible is None or part.visible is guard.signed_in):
                raise at.fail("`visible=` on a section of a record page takes a permission.",
                              "A section's heading always shows. To hide the section from some users write "
                              "`visible=ui.read(Model)`; to show different content by state, put `ui.switch(...)` "
                              "inside the section.")
            content = part.body(at)
            built.append(_rfds(at, lambda part=part, ident=ident, content=content, needs=needs: templates.ObjectSection(
                id=ident, title_id=part.title, title_default=part.title, content=content, permission=needs)))
        return built

    def _page(self, ctx: Build) -> rx.Component:
        import rfds
        from rfds import templates

        head: list = []
        if self.status is not None:
            given = take(ctx.at("status"), self.status, (vocab.Status,), "`status=` holds one `ui.status(word, tone=)`.")
            if len(given) != 1:
                raise ctx.at("status").fail(f"got {len(given)} badges.", "`status=` holds one `ui.status(word, tone=)`.")
            (badge,) = given
            head.append(badge.build(ctx.at("status")))
        facts = take(ctx.at("facts"), list(self.facts), (vocab.Fact,), "Use `ui.fact(label, value)`.")
        if facts:
            head.append(rfds.key_value(*vocab.build_all(ctx.at("facts"), facts), columns=min(3, len(facts))))
        if self.load is not None:
            ctx = dataclasses.replace(ctx, under_load=True)
        title, actions, sections = self._title(ctx), self._buttons(ctx), self._sections(ctx)   # lex's checks first
        header = rfds.stack(*head) if head else rx.fragment()
        page = _rfds(ctx, lambda: templates.object_page(
            title=title, path=ctx.route or "/", header=header, sections=sections,
            navigation="tabs" if self.tabs and len(sections) > 1 else "anchor", actions=actions))
        return states(ctx, self.load, page, "rect")


def record(*, title: Any, load: Any = None, status: Any = None, facts: Any = (), sections: Any = (),
           tabs: bool = False, actions: Any = ()) -> Record:
    """One thing: its key facts, then its parts.

    ``title`` is a sentence or a state value. ``load=`` is the ``@ui.load`` that fills the page;
    until it has finished the page shows a skeleton, and its failure in place. ``status=`` is one
    ``ui.status``; ``facts=`` a list of ``ui.fact``; ``sections=`` a list of ``ui.section``.

    The first of ``actions`` is the primary one, unless it is dangerous or a link; one further
    action is a button, and two or more further actions go into a "More actions" menu. To open a
    page from ``actions=``, use ``ui.button(label, to=page)``. ``tabs=True`` shows one section at
    a time.
    """
    return Record(title, load, status, _many(facts), _many(sections), bool(tabs), _many(actions))


@dataclasses.dataclass(eq=False)
class List(Kind):
    title: Any
    load: Any = None
    table: Any = None
    actions: tuple = ()

    def _page(self, ctx: Build) -> rx.Component:
        from rfds import templates

        if not isinstance(self.table, tables.Table):
            raise ctx.at("table").fail(f"got {called(self.table)}.",
                                       "`table=` holds one `ui.table(Model, columns=[...])`.")
        title, actions = self._title(ctx), self._buttons(ctx)
        named = dataclasses.replace(ctx.at("table"), caption=title if isinstance(title, str) else "",
                                    under_load=ctx.under_load or self.load is not None, page_search=True)
        table = self.table.build(named)
        page = _rfds(ctx, lambda: templates.list_report(title=title, path=ctx.route or "/", table=table, actions=actions))
        return states(ctx, self.load, page, "table")


def list_(*, title: Any, load: Any = None, table: Any = None, actions: Any = ()) -> List:
    """Finding things: a table to search, sort and open from. ``actions`` are placed as on a record page."""
    return List(title, load, table, _many(actions))
