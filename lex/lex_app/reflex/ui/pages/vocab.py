"""The vocabulary: the components a slot accepts. None takes a styling argument."""

from __future__ import annotations

import dataclasses
from typing import Any, Callable

import reflex as rx

from lex.lex_app.reflex.ui.pages import links
from lex.lex_app.reflex.ui.pages.parts import Build, NotASentence, Part, called, say, shown

TONES = ("neutral", "info", "success", "warning", "danger")
#: What a value can be, for display and for input alike (a form's fields take the same words).
KINDS = ("text", "number", "money", "percent", "date", "datetime", "year", "bool", "choice", "choices")


def _tone(tone: str) -> str:
    if tone not in TONES:
        raise ValueError(f"tone= must be one of {', '.join(TONES)}; got {tone!r}")
    return tone


def _whole(value: Any) -> bool:
    """Whether ``value`` is a whole number: an int, or a state value typed int."""
    if isinstance(value, rx.Var):
        return value._var_type is int
    return isinstance(value, int) and not isinstance(value, bool)


def shape(value: Any, kind: str, currency: Any = None) -> Any:
    """``value`` shown as its ``kind``, formatted for the user's locale."""
    import rfds

    if kind in ("text", "choice"):
        return value
    if kind == "choices":               # several chosen: "north, south"
        if isinstance(value, rx.Var):
            return value.to(list[str]).join(", ")
        return ", ".join(str(one) for one in value or ())
    if kind == "year":                  # a year is not a quantity: no thousands separator
        return value.to(str) if isinstance(value, rx.Var) else str(value)
    if kind == "bool":
        return rx.cond(value, say("Yes"), say("No"))
    if kind == "date":
        from rfds import fmt_js

        return fmt_js.date(value if isinstance(value, (str, rx.Var)) else value.isoformat())
    if kind == "number":
        return rfds.number(value, digits=0 if _whole(value) else 2)
    if kind == "money":
        return rfds.money(value, currency)
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
    """A sentence or a paragraph, written in English; it is translated for the user.

    ``{name}`` in it is filled from ``values`` (a value cannot be called ``visible``). A state
    value is shown as it is.
    """
    return Text(sentence, values, visible)


@dataclasses.dataclass(eq=False)
class Fact(Part):
    label: Any
    value: Any
    kind: str = "text"
    visible: Any = None
    currency: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.kv_item(say(self.label), shape(self.value, self.kind, self.currency))


def fact(label: Any, value: Any, *, kind: str = "text", currency: Any = None, visible: Any = None) -> Fact:
    """A label and its value.

    ``kind`` says what the value is, and lex-app formats it for the user's locale:
    ``"number"`` (a whole number without decimals, any other with two), ``"money"`` (in
    ``currency=``, a code such as ``"EUR"`` or a state value; else the home currency),
    ``"percent"`` (a ratio: 0.6 shows 60%), ``"date"``, ``"datetime"`` (an instant with its time
    zone, shown in the user's), ``"year"`` (no thousands separator), ``"bool"`` (Yes or No),
    ``"choice"`` or ``"text"`` (shown as they are), ``"choices"`` (a list, shown joined by commas).
    A form's fields take the same words.
    """
    if kind not in KINDS:
        raise ValueError(f"kind= must be one of {', '.join(KINDS)}; got {kind!r}")
    if currency is not None and kind != "money":
        raise ValueError(f"ui.fact(currency=) goes with kind=\"money\"; got kind={kind!r}")
    return Fact(label, value, kind, visible, currency)


@dataclasses.dataclass(eq=False)
class Status(Part):
    word: Any
    tone: str = "neutral"
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        return rfds.lozenge(say(self.word), tone=self.tone)


def status(word: Any, *, tone: str = "neutral", visible: Any = None) -> Status:
    """A small badge: one word about the thing's state. ``tone`` is one of the tones:
    neutral, info, success, warning, danger."""
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
    """A notice in place. ``{name}`` in the sentence is filled from ``values`` (not ``tone`` or ``visible``)."""
    return Message(sentence, values, _tone(tone), visible)


def _marked(inner: rx.Component, wrapper: rx.Component) -> rx.Component:
    """``wrapper`` carrying the RFDS metadata of the control it wraps.

    The one place lex.ui reaches into RFDS's private metadata. RFDS's page header reads a
    control's kind and intent from it -- which action is the primary one, what is a menu entry --
    and a ``visible=`` wrapper has none of its own, so the control would lose its role.
    """
    from rfds.components.actions._common import get_meta, set_meta

    return wrapper if wrapper is inner else set_meta(wrapper, **get_meta(inner))


@dataclasses.dataclass(eq=False)
class Link(Part):
    label: Any
    to: Any = None
    values: dict = dataclasses.field(default_factory=dict)
    record: Any = None
    href: str | None = None
    visible: Any = None
    icon: str | None = None             # in lex-app's frame: the entry's icon
    badge: Any = None                   # in lex-app's frame: a count beside the entry
    requires: tuple | None = None       # in lex-app's frame: who the entry is drawn for

    def target(self, ctx: Build) -> tuple[Any, bool]:
        """Where the link leads, and whether that is an outside site."""
        if self.to is not None:
            return links.href(ctx, self.to, self.values), False
        if self.record is not None:
            from lex.lex_app.reflex.embed import lex_app_base_url

            model, pk = self.record
            return (lex_app_base_url().rstrip("/") + "/" + model._meta.model_name + "/"
                    + links.piece(ctx, "pk", pk, instead="Write `record=(Model, row.id)`.") + "/show"), True
        return self.href, True

    def click(self, ctx: Build) -> Any:
        self.in_page(ctx)
        where, outside = self.target(ctx)
        return rx.redirect(where, is_external=outside)

    def in_page(self, ctx: Build) -> None:
        """Refuse the frame's words (``badge=``, ``requires=``, ``icon=``) on a link in a page."""
        if self.badge is not None:
            raise ctx.fail(f"the link `{self.label}` has `badge=`, a count beside an entry of lex-app's frame.",
                           "Show a count in a page with a state value: `ui.text(\"{count} waiting\", count=State.waiting)`.")
        if self.requires is not None:
            raise ctx.fail(f"the link `{self.label}` has `requires=`, which decides an entry of lex-app's frame on the "
                           "server.", "In a page, `visible=` decides what is shown -- `visible=ui.read(Model)` or a "
                           "state value -- and the page's handlers what is allowed.")
        if self.icon is not None:
            raise ctx.fail(f"the link `{self.label}` has `icon=`, the icon of an entry of lex-app's frame.",
                           "In a page a link is its words; a button has an icon: `ui.button(\"Search\", to=page, "
                           "icon=\"search\")`.")

    def menu_entry(self, ctx: Build) -> rx.Component:
        return menu_entry(ctx, self.label, self.click(ctx), self.visible)

    def _wrap(self, ctx: Build, component: rx.Component) -> rx.Component:
        return _marked(component, shown(ctx, component, self.visible))

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        self.in_page(ctx)
        where, outside = self.target(ctx)
        if ctx.place == "actions":
            return rfds.button(say(self.label), intent="secondary", on_click=rx.redirect(where, is_external=outside))
        return rfds.link(say(self.label), href=where, external=outside)


def link(label: Any, *, to: Any = None, record: Any = None, href: str | None = None, visible: Any = None,
         icon: str | None = None, badge: Any = None, requires: Any = None, **values: Any) -> Link:
    """Goes somewhere: ``to=`` a page (with its parameters by name), ``record=(Model, pk)``, or ``href=`` an outside site.

    In lex-app's frame (``ui.frame``: the navigation, the user menu, the header) a link is an entry and
    takes three words more: ``icon=`` (an RFDS icon), ``badge=`` (a count beside it: a function of the
    user, or a whole-number state value) and ``requires=`` (what ``requires=`` takes everywhere: the
    entry is drawn only for users who meet it, decided on the server; a link to a page that has a
    ``requires=`` is guarded by it as well). In a page they are refused: there ``visible=`` decides what
    is shown, and the handlers what is allowed.
    """
    given = [name for name, value in (("to", to), ("record", record), ("href", href)) if value is not None]
    if len(given) != 1:
        raise ValueError("ui.link() takes exactly one of to=, record= or href=; got " + (", ".join(given) or "none")
                         + ". Write `ui.link(\"Open\", to=some_page, key=...)`.")
    if values and to is None:
        raise ValueError(f"ui.link(): {', '.join(values)} can only be given with to=, as the page's parameters.")
    if href is not None and not (isinstance(href, str) and href.startswith(("http://", "https://"))):
        raise ValueError(f"ui.link(href=) is for an outside site and takes a fixed address starting with https://; "
                         f"got {href!r}. For a page of the dashboards use to=. Mail and phone links are not supported yet.")
    if record is not None and not (isinstance(record, tuple) and len(record) == 2 and hasattr(record[0], "_meta")
                                   and record[1] is not None):
        raise ValueError("ui.link(record=) takes (Model, pk): `record=(Fund, row.id)`.")
    if icon is not None and (not isinstance(icon, str) or not icon):
        raise ValueError(f"ui.link(icon=) takes the name of an RFDS icon, such as icon=\"search\"; got {icon!r}.")
    if requires is not None:
        from lex.lex_app.reflex.ui import guard

        try:
            requires = guard.normalise(requires)
        except (TypeError, ValueError) as wrong:
            raise TypeError(f"ui.link(requires=): {wrong}") from None
    return Link(label, to, values, record, href, visible, icon, badge, requires)


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
    args: tuple = ()                    # values passed to the handler (a row's key)
    disabled: Any = None                # why it cannot be pressed now ("" or None: it can)
    confirm: Any = None                 # the question asked before the handler runs
    ask: tuple = ()                     # the fields of a small dialog whose values go to the handler

    def click(self, ctx: Build) -> Any:
        if self.to is not None:
            return rx.redirect(links.href(ctx, self.to, self.values))
        if self.confirm is not None or self.ask:
            from lex.lex_app.reflex.ui.pages import forms

            return forms.dialog(ctx, self)
        if self.tone == "danger":
            raise ctx.fail(f"the button `{self.label}` has `tone=\"danger\"` and no `confirm=`.",
                           "Ask before it runs, naming the action and its object: "
                           "`ui.button(\"Close order\", State.close, tone=\"danger\", confirm=\"Close this order?\")`.")
        return self.handler(*self.args) if self.args else self.handler

    def menu_entry(self, ctx: Build) -> rx.Component:
        return menu_entry(ctx, self.label, self.click(ctx), self.visible, tone=self.tone, icon=self.icon)

    def _wrap(self, ctx: Build, component: rx.Component) -> rx.Component:
        return _marked(component, shown(ctx, component, self.visible))

    def _render(self, ctx: Build) -> rx.Component:
        import rfds

        intent = "danger" if self.tone == "danger" else "primary" if self.primary else "secondary"
        extra: dict[str, Any] = {}
        if getattr(getattr(self.handler, "fn", None), "__ui_kind__", "") in ("action", "job"):
            from lex.lex_app.reflex.ui import runs

            runs.name_job(self.handler, self.label)     # its refusals name the job as this button does
            # The button knows its handler: it shows it running -- for a row's button this row's run, not another's.
            extra["loading"] = runs.running(self.handler, *self.args[:1])
        if isinstance(self.disabled, rx.Var):           # a reason per row, or from the state; "" lets it be pressed
            if self.disabled._var_type in (bool, int, float):
                raise ctx.fail(f"a button's `disabled=` is a {self.disabled._var_type.__name__} from the state.",
                               "Give the reason as text: a field holding the sentence, and \"\" when the "
                               "button can be pressed.")
            reason = self.disabled.to(str)
            extra["disabled"] = reason != ""
            extra["disabled_reason"] = rx.cond(reason != "", reason, None)
        elif self.disabled:
            extra["disabled"], extra["disabled_reason"] = True, say(self.disabled)
        return rfds.button(say(self.label), intent=intent, icon=self.icon, on_click=self.click(ctx), **extra)


def button(label: Any, handler: Any = None, *args: Any, to: Any = None, confirm: Any = None, ask: Any = (),
           tone: str = "neutral", icon: str | None = None, visible: Any = None, disabled: Any = None,
           **values: Any) -> Button:
    """Runs a handler, or opens a page (``to=``, with the page's parameters by name).

    A button shows its handler running. ``confirm="Close this order?"`` asks that question before
    the handler runs; a ``tone="danger"`` button must have one. ``ask=[ui.field("reason",
    required=True)]`` opens a small dialog with those fields; the handler receives what was
    entered as its last argument, ``values``, checked as a form's values are::

        @ui.action
        def decline(self, key, values):
            orders.decline(key, values["reason"])

        ui.button("Decline", OrdersState.decline, row["id"], ask=[ui.field("reason", required=True)])

    Values after the handler are passed to it: in a table's ``row_actions``,
    ``ui.button("Approve", State.approve, row["id"])``; the button then shows that row's run.
    The values come back from the browser: check them in the handler.

    ``disabled=`` says why it cannot be pressed now: the button stays in its place, greyed, and
    shows the reason when pointed at or focused. Give the sentence, or a field holding it -
    ``disabled=row["blocked"]``, where ``""`` lets that row's button be pressed. Like ``visible=``,
    it decides what is shown: the handler still checks.
    """
    if args and handler is None:
        raise ValueError("ui.button(): values after the label go to a handler; give the handler first.")
    if (handler is None) == (to is None):
        raise ValueError("ui.button() takes a handler or to=, one of them: `ui.button(\"Close fund\", State.close)` "
                         "or `ui.button(\"New fund\", to=new_fund)`.")
    if values and to is None:
        raise ValueError(f"ui.button(): {', '.join(values)} can only be given with to=, as the page's parameters.")
    if disabled is not None and not isinstance(disabled, (str, rx.Var)):
        raise TypeError(f"ui.button(disabled=) takes why it cannot be pressed (a sentence, or a field holding one); "
                        f"got {disabled!r}")
    asked = () if ask is None else tuple(ask) if isinstance(ask, (list, tuple)) else (ask,)
    if (confirm is not None or asked) and handler is None:
        raise ValueError("ui.button(): confirm= and ask= come before a handler runs; a button with to= opens a page.")
    if confirm is not None and not (isinstance(confirm, (str, rx.Var)) and (isinstance(confirm, rx.Var) or confirm.strip())):
        raise TypeError(f"ui.button(confirm=) takes the question to ask: `confirm=\"Close this order?\"`; got {confirm!r}")
    return Button(label, handler, to, values, _tone(tone), icon, visible, False, tuple(args),
                  disabled if isinstance(disabled, rx.Var) else (disabled or None), confirm, asked)


def menu_entry(ctx: Build, label: Any, click: Any, visible: Any, *, tone: str = "neutral",
               icon: str | None = None) -> rx.Component:
    """An action as an entry of the page header's "More actions" menu."""
    from rfds.components.actions.menu import menu_item

    try:
        words = say(label)
    except NotASentence:
        raise ctx.fail(f"an action's label is {called(label)}.",
                       "Write the words: `ui.button(\"Close fund\", ...)`.") from None
    entry = menu_item(words, on_select=click, icon=icon, tone="danger" if tone == "danger" else "neutral")
    return _marked(entry, shown(ctx, entry, visible))


@dataclasses.dataclass(eq=False)
class CustomBlock(Part):
    ui_name = "custom_block"

    component: Any
    reason: str
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        return self.component


def custom_block(component: Any, *, reason: str, visible: Any = None) -> CustomBlock:
    """The marked way out: a raw component, with one sentence saying why the vocabulary did not do."""
    if not isinstance(component, rx.Component):
        raise TypeError(f"ui.custom_block() takes a component, got {type(component).__name__}")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("ui.custom_block() needs reason=: one sentence saying why the vocabulary did not do")
    return CustomBlock(component, reason.strip(), visible)


@dataclasses.dataclass(eq=False)
class Each(Part):
    items: Any
    render: Callable[[Any], Part]
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        def one(item: Any) -> rx.Component:
            part = self.render(item)
            if not isinstance(part, Part):
                raise ctx.fail("the function given to `ui.each` did not return a lex component.",
                               "Return one of the `ui.*` components, or `ui.custom_block(...)`.")
            _fits(ctx, part, "ui.each")
            return part.build(ctx)

        return rx.foreach(self.items, one)


def each(items: Any, render: Callable[[Any], Part], *, visible: Any = None) -> Each:
    """Repeats ``render`` for every item: a function of one item that returns a ``ui.*`` component."""
    if not callable(render):
        raise TypeError("ui.each(items, render) takes a function that returns a `ui.*` component for one item; "
                        f"got {type(render).__name__}")
    return Each(items, render, visible)


@dataclasses.dataclass(eq=False)
class Switch(Part):
    value: Any
    cases: dict
    visible: Any = None

    def _render(self, ctx: Build) -> rx.Component:
        for part in self.cases.values():
            if not isinstance(part, Part):
                raise ctx.fail("a case of `ui.switch` is not a lex component.",
                               "Give each case one of the `ui.*` components, or `ui.custom_block(...)`.")
            _fits(ctx, part, "ui.switch")
        return rx.match(self.value, *[(key, part.build(ctx)) for key, part in self.cases.items()], rx.fragment())


def switch(value: Any, cases: dict, *, visible: Any = None) -> Switch:
    """Shows the case whose key equals ``value``; nothing when none does. The keys are fixed values."""
    if not cases:
        raise ValueError("ui.switch(value, cases) needs at least one case: `{\"open\": ui.text(...)}`")
    return Switch(value, dict(cases), visible)


def _fits(ctx: Build, part: Part, where: str) -> None:
    """Refuse a part that cannot sit inside a section, when `ui.each` or `ui.switch` produced it."""
    if not type(part).nests:
        raise ctx.fail(f"`{where}` produced {called(part)}, and sections do not nest.",
                       "Give the page each section itself, in `sections=[...]`.")


CONTENT = (Text, Fact, Status, Message, Link, Button, CustomBlock, Each, Switch)


def build_all(ctx: Build, parts: list) -> list:
    return [part.build(ctx) for part in parts]
