"""What a page is made of: parts that describe, and the context they are built in."""

from __future__ import annotations

import dataclasses
import re
from typing import Any

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


class Part:
    """Something a slot accepts. A part only describes; :meth:`build` makes the component."""

    visible: Any = None

    def build(self, ctx: Build) -> rx.Component:
        return shown(self._render(ctx), self.visible)

    def _render(self, ctx: Build) -> rx.Component:
        raise NotImplementedError


def shown(component: rx.Component, visible: Any) -> rx.Component:
    """``component``, shown only when ``visible`` holds: a permission, or a state value."""
    if visible is None:
        return component
    from lex.lex_app.reflex.ui import guard

    if isinstance(visible, guard.Requirement):
        from rfds.auth import can

        from lex.lex_app.reflex.access import permission

        return can(permission(visible.target, visible.scope), component)
    return rx.cond(visible, component)


def say(sentence: Any, **values: Any) -> Any:
    """A sentence the user reads, translated in the browser. A state value is shown as it is."""
    if isinstance(sentence, rx.Var):
        return sentence
    if not isinstance(sentence, str) or not sentence:
        raise TypeError(f"expected a sentence, got {sentence!r}")
    import rfds

    return rfds.t(sentence, sentence, **values)


def slug(text: str) -> str:
    return re.sub(r"[^0-9a-z]+", "-", text.lower()).strip("-") or "part"


def called(value: Any) -> str:
    """How an unexpected value is named in a message."""
    if isinstance(value, rx.Component):
        return f"a raw component (`{type(value).__name__}`)"
    if isinstance(value, Part):
        return f"`ui.{type(value).__name__.lower()}`"
    return f"`{value!r}`"[:80]


def take(ctx: Build, value: Any, kinds: tuple[type, ...], instead: str) -> list:
    """The parts given for a list slot, checked: each must be one of ``kinds``."""
    items = [] if value is None else list(value) if isinstance(value, (list, tuple)) else [value]
    for item in items:
        if not isinstance(item, kinds):
            raise ctx.fail(f"got {called(item)}.", instead)
    return items
