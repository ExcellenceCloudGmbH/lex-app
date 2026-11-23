"""The vocabulary: the components a slot accepts. None takes a styling argument."""

from __future__ import annotations

import dataclasses
from typing import Any

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, Part, say

TONES = ("neutral", "info", "success", "warning", "danger")
KINDS = ("text", "number", "money", "percent", "datetime")


def _tone(tone: str) -> str:
    if tone not in TONES:
        raise ValueError(f"tone= must be one of {', '.join(TONES)}; got {tone!r}")
    return tone


def shape(value: Any, kind: str) -> Any:
    """``value`` shown as its ``kind``, formatted for the user's locale."""
    import rfds

    if kind == "text":
        return value
    if kind == "number":
        return rfds.number(value, digits=0)
    if kind == "money":
        return rfds.money(value)
    if kind == "percent":
        return rfds.percent(value)
    if kind == "datetime":
        return rfds.timestamp(value)
    raise ValueError(f"kind= must be one of {', '.join(KINDS)}; got {kind!r}")


@dataclasses.dataclass(eq=False)
class Text(Part):
    sentence: Any
    values: dict
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        return rx.text(say(self.sentence, **self.values))


def text(sentence: Any, *, visible: Any = None, **values: Any) -> Text:
    """A sentence or a paragraph. ``{name}`` in it is filled from ``values``."""
    return Text(sentence, values, visible)


@dataclasses.dataclass(eq=False)
class Fact(Part):
    label: Any
    value: Any
    kind: str = "text"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.kv_item(say(self.label), shape(self.value, self.kind))


def fact(label: Any, value: Any, *, kind: str = "text", visible: Any = None) -> Fact:
    """A label and its value."""
    if kind not in KINDS:
        raise ValueError(f"kind= must be one of {', '.join(KINDS)}; got {kind!r}")
    return Fact(label, value, kind, visible)


@dataclasses.dataclass(eq=False)
class Status(Part):
    word: Any
    tone: str = "neutral"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.lozenge(say(self.word), tone=self.tone)


def status(word: Any, *, tone: str = "neutral", visible: Any = None) -> Status:
    """A small badge."""
    return Status(word, _tone(tone), visible)


@dataclasses.dataclass(eq=False)
class Message(Part):
    sentence: Any
    values: dict
    tone: str = "info"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.inline_message(say(self.sentence, **self.values), tone=self.tone)


def message(sentence: Any, *, tone: str = "info", visible: Any = None, **values: Any) -> Message:
    """A notice in place."""
    return Message(sentence, values, _tone(tone), visible)


@dataclasses.dataclass(eq=False)
class Link(Part):
    label: Any
    to: Any = None
    values: dict = dataclasses.field(default_factory=dict)
    record: Any = None
    href: str | None = None
    visible: Any = None

    def target(self, ctx: Build) -> tuple[Any, bool]:
        """Where the link leads, and whether that is an outside site."""
        if self.to is not None:
            return links.href(ctx, self.to, self.values), False
        if self.record is not None:
            from lex.lex_app.reflex.embed import lex_app_base_url

            model, pk = self.record
            return lex_app_base_url().rstrip("/") + "/" + model._meta.model_name + "/" + links.as_text(pk) + "/show", True
        return self.href, True

    def click(self, ctx: Build) -> Any:
        where, outside = self.target(ctx)
        return rx.redirect(where, is_external=outside)

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        where, outside = self.target(ctx)
        if ctx.place == "actions":
            return rfds.button(say(self.label), intent="secondary", on_click=rx.redirect(where, is_external=outside))
        return rfds.link(say(self.label), href=where, external=outside)


def link(label: Any, *, to: Any = None, record: Any = None, href: str | None = None, visible: Any = None,
         **values: Any) -> Link:
    """Goes somewhere: ``to=`` a page (with its parameters by name), ``record=(Model, pk)``, or ``href=`` an outside site."""
    given = [name for name, value in (("to", to), ("record", record), ("href", href)) if value is not None]
    if len(given) != 1:
        raise ValueError("ui.link() takes exactly one of to=, record= or href=; got " + (", ".join(given) or "none"))
    if href is not None and "://" not in href:
        raise ValueError(f"ui.link(href=) is for an outside site (https://...); got {href!r}. Use to= for a page.")
    return Link(label, to, values, record, href, visible)


@dataclasses.dataclass(eq=False)
class Button(Part):
    label: Any
    handler: Any = None
    to: Any = None
    values: dict = dataclasses.field(default_factory=dict)
    tone: str = "neutral"
    icon: str | None = None
    visible: Any = None
    primary: bool = False               # set by the kind: the first action of a page

    def click(self, ctx: Build) -> Any:
        return rx.redirect(links.href(ctx, self.to, self.values)) if self.to is not None else self.handler

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        intent = "danger" if self.tone == "danger" else "primary" if self.primary else "secondary"
        return rfds.button(say(self.label), intent=intent, icon=self.icon, on_click=self.click(ctx))


def button(label: Any, handler: Any = None, *, to: Any = None, tone: str = "neutral", icon: str | None = None,
           visible: Any = None, **values: Any) -> Button:
    """Runs a handler, or opens a page (``to=``)."""
    if (handler is None) == (to is None):
        raise ValueError("ui.button() takes a handler or to=, one of them")
    return Button(label, handler, to, values, _tone(tone), icon, visible)


def menu_entry(ctx: Build, label: Any, click: Any, visible: Any) -> rx.Component:
    """An action as an entry of the page header's "More actions" menu."""
    import rfds
    from rfds.components.actions._common import get_meta, set_meta

    from lex.lex_app.reflex.ui.pages.parts import shown

    from rfds.components.actions.menu import menu_item

    entry = menu_item(say(label), on_select=click)
    return set_meta(shown(entry, visible), **get_meta(entry))     # RFDS keeps what it recognises as a menu entry
