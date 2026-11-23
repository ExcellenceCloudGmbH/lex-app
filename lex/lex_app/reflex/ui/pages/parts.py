"""What a page is made of: parts that describe, and the context they are built in."""

from __future__ import annotations

import dataclasses
import re
from typing import Any, ClassVar

import reflex as rx


class PageError(Exception):
    """A page is declared wrongly. The message names the page and the slot and says what to write."""


@dataclasses.dataclass
class Build:
    """Where a part is being built: which page, which slot, and how things there look."""

    page: str                           # the page function's name
    route: str | None = None            # None on a dashboard
    slot: str = ""                      # the slot being filled, for messages
    place: str = "body"                 # "body" or "actions": a link in actions is a button
    mounts: bool = False                # a dashboard: a load starts when its part mounts

    def at(self, slot: str, place: str | None = None) -> "Build":
        return dataclasses.replace(self, slot=slot, place=place or self.place)

    def fail(self, problem: str, instead: str) -> PageError:
        where = f"Page `{self.page}`" + (f", slot `{self.slot}`" if self.slot else "")
        return PageError(f"{where}: {problem}\n{instead}")


class NotASentence(TypeError):
    """A text argument is not a sentence. :meth:`Part.build` turns it into a :class:`PageError` for its page."""

    def __init__(self, value: Any):
        super().__init__(f"expected a sentence, got {value!r}")
        self.value = value


class Part:
    """Something a slot accepts. A part only describes; :meth:`build` makes the component."""

    #: The public name, when it is not the class name in lower case (``custom_block``).
    ui_name: ClassVar[str] = ""
    #: Whether the part may sit inside a section. A section may not: sections do not nest.
    nests: ClassVar[bool] = True
    visible: Any = None

    def build(self, ctx: Build) -> rx.Component:
        try:
            component = self._render(ctx)
        except NotASentence as wrong:
            raise ctx.fail(f"{called(self)} got {called(wrong.value)} where its words belong.",
                           "Write the sentence the user reads, in English: `ui.text(\"3 open requests\")`. "
                           "A state value is shown as it is.") from None
        return shown(ctx, component, self.visible)

    def _render(self, ctx: Build) -> rx.Component:
        raise NotImplementedError


def shown(ctx: Build, component: rx.Component, visible: Any) -> rx.Component:
    """``component``, shown only when ``visible`` holds.

    ``visible`` is a permission (what ``requires=`` takes: ``ui.read(Model)``...), a list of
    them (all must hold), a state value, or a plain ``True``/``False``. It decides what is
    shown, never what is allowed: the handler still enforces.
    """
    from lex.lex_app.reflex.ui import guard

    if visible is None or visible is guard.signed_in:      # every user of the dashboards is signed in
        return component
    if isinstance(visible, guard.Requirement):
        from rfds.auth import can

        from lex.lex_app.reflex.access import permission

        return can(permission(visible.target, visible.scope), component)
    if isinstance(visible, (list, tuple)):
        if not visible:
            raise ctx.fail("`visible=` got an empty list.", "List at least one permission: `visible=[ui.read(Model)]`.")
        for one in visible:
            component = shown(ctx, component, one)
        return component
    if isinstance(visible, (bool, rx.Var)):
        return rx.cond(visible, component)
    raise ctx.fail(f"`visible=` got {called(visible)}.",
                   "Give a permission -- `visible=ui.edit(Model)` -- or a state value: `visible=State.is_open`.")


def say(sentence: Any, **values: Any) -> Any:
    """A sentence the user reads, translated in the browser. A state value is shown as it is.

    The sentence is the message: it is passed to RFDS's translation as the id and as the English
    text. ``{name}`` in it is filled from ``values``; a literal brace is written ``'{'``.
    """
    if isinstance(sentence, rx.Var):
        return sentence
    if not isinstance(sentence, str) or not sentence:
        raise NotASentence(sentence)
    import rfds

    return rfds.t(sentence, sentence, **values)


def slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", text.lower()).strip("-") or "part"


def called(value: Any) -> str:
    """How an unexpected value is named in a message."""
    if isinstance(value, rx.Component):
        return f"a raw component (`{type(value).__name__}`)"
    if isinstance(value, Part):
        return f"`ui.{type(value).ui_name or type(value).__name__.lower()}`"
    if value is None:
        return "nothing (`None`)"
    text = repr(value)
    return f"`{text[:60]}...`" if len(text) > 60 else f"`{text}`"


def take(ctx: Build, value: Any, kinds: tuple[type, ...], instead: str) -> list:
    """The parts given for a list slot, checked: each must be one of ``kinds``."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if not isinstance(item, kinds):
            raise ctx.fail(f"got {called(item)}.", instead)
    return items
