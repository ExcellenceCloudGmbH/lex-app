"""Forms: ``ui.field``, ``ui.step``, the ``ui.form`` page kind, and the dialog of a button that asks.

A form is submitted once, to one handler, which receives the values as a dictionary keyed by each
field's key. Nothing is sent while the user types. What the server checks before the handler's body
runs is in :mod:`lex.lex_app.reflex.ui.forms`; this module draws the fields with RFDS's inputs.
"""

from __future__ import annotations

import dataclasses
import datetime
import inspect
import itertools
from typing import Any, ClassVar

import reflex as rx

from lex.lex_app.reflex.ui import forms as rules
from lex.lex_app.reflex.ui import rows, runs
from lex.lex_app.reflex.ui.pages import kinds, tables, vocab
from lex.lex_app.reflex.ui.pages.parts import Build, Part, called, say, shown, slug, take

#: Up to this many fixed options are a dropdown (several: boxes to tick); more, or options from the
#: state, a box to type in (several: RFDS's multi-select). RFDS draws the line at the same number.
DROPDOWN_OPTIONS = 12
_CHOICES = ("choice", "choices")

_FIELD = "`ui.field(key, label=, kind=, options=, required=, help=, value=)`"
_DIALOGS = itertools.count(1)


@dataclasses.dataclass(eq=False)
class Field(Part):
    nests: ClassVar[bool] = False       # a field belongs to a form, never to a section

    key: str
    label: Any = None
    kind: str | None = None
    options: Any = None
    required: bool = False
    help: Any = None
    value: Any = None
    currency: Any = None
    lines: int | None = None

    @property
    def searches(self) -> bool:
        """Whether the options are searched on the server: ``options=`` is a ``@ui.rows`` handler."""
        return rows.is_rows(self.options)

    def searched_id(self, handler: Any) -> str:
        """The field's id among searched fields: the handler that receives it, and its key."""
        return f"{rules.ident(handler)}|{self.key}"

    @property
    def is_a(self) -> str:
        """The field's kind: as given; a field with options is a choice; else text."""
        return self.kind or ("choice" if self.options is not None else "text")

    def words(self) -> Any:
        """The label: as given, else the key with a capital (``"valid_from"`` is "Valid from")."""
        return say(self.label if self.label is not None else self.key.replace("_", " ").capitalize())

    def check(self, ctx: Build) -> None:
        """Refuse a field that is declared wrongly, naming the page and the slot."""
        kind = self.is_a
        if kind == "file":
            raise ctx.fail(f"field `{self.key}` is a file.",
                           "A file field is not built yet: an upload is a `ui.custom_block(..., reason=\"...\")`.")
        if kind not in rules.KINDS:
            raise ctx.fail(f"field `{self.key}` has the kind `{kind}`.",
                           f"`kind=` is one of {', '.join(rules.KINDS)}.")
        if self.options is not None and kind not in _CHOICES:
            raise ctx.fail(f"field `{self.key}` has `options=` and the kind `{kind}`.",
                           "Only a choice has options: leave `kind=` out for one of them, or write "
                           "`kind=\"choices\"` for several.")
        kind_of = getattr(getattr(self.options, "fn", None), "__ui_kind__", "")
        if kind_of and kind_of != "rows":
            body = self.options.fn.__ui_fn__
            raise ctx.fail(f"field `{self.key}` got `{'.'.join(body.__qualname__.split('.')[-2:])}`, an "
                           f"`@ui.{kind_of}`, as its options.", "Options searched on the server come from a "
                           "`@ui.rows` handler: `options=OrdersState.customers`.")
        if self.searches and kind == "choices":
            raise ctx.fail(f"field `{self.key}` chooses several from `{self.options.fn.__ui_key__}`, a search.",
                           "A search offers one choice: leave `kind=` out. Several are chosen from `options=` a "
                           "list or a field of the state.")
        if self.searches and self.value is not None:
            raise ctx.fail(f"field `{self.key}` is searched on the server and starts as {called(self.value)}.",
                           "A searched choice starts empty -- the server accepts only what it offered: "
                           "leave `value=` out.")
        if kind in _CHOICES and self.options is None:
            raise ctx.fail(f"field `{self.key}` is a choice without `options=`.",
                           "Give what can be chosen: `options=[\"open\", \"closed\"]`, or "
                           "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them.")
        if self.currency is not None and kind != "money":
            raise ctx.fail(f"field `{self.key}` has `currency=` and the kind `{kind}`.",
                           "`currency=` goes with `kind=\"money\"`.")
        if self.lines is not None and kind != "text":
            raise ctx.fail(f"field `{self.key}` has `lines=` and the kind `{kind}`.",
                           "`lines=` goes with text, the default kind: leave `kind=` out.")
        if kind == "bool" and not (self.value is None or isinstance(self.value, (bool, rx.Var))):
            raise ctx.fail(f"field `{self.key}` is a yes/no and starts as {called(self.value)}.",
                           "`value=` is `True`, `False` or a state value.")
        if kind == "choices" and not (self.value is None or isinstance(self.value, rx.Var) or (
                isinstance(self.value, (list, tuple)) and all(isinstance(one, str) for one in self.value))):
            raise ctx.fail(f"field `{self.key}` chooses several and starts as {called(self.value)}.",
                           "`value=` is a list of options, such as `value=[\"north\"]`, or a state value.")

    def choices(self, ctx: Build, handler: Any) -> tuple[Any, tuple | None, str]:
        """A choice's options as (RFDS takes them, the fixed values the server accepts, the state field holding them)."""
        given = self.options
        if isinstance(given, rx.Var):
            data = given._get_all_var_data()
            if data is None or not data.field_name or data.state != handler.state_full_name:
                raise ctx.fail(f"field `{self.key}` takes its options from another state.",
                               "The options are a fixed list, or a field of the state whose handler receives "
                               "the form: lex-app checks the chosen value against it.")
            listed = rx.Var(_js_expr=f"(({given!s}) || []).map((one) => ({{value: String(one), label: String(one)}}))",
                            _var_type=list[dict[str, str]], _var_data=data)
            return listed, None, data.field_name.removesuffix("_rx_state_")
        pairs = list(given.items()) if isinstance(given, dict) else [(one, None) for one in (given or ())]
        values = [value for value, _ in pairs]
        if (not pairs or isinstance(given, str) or len(set(values)) != len(values)
                or not all(isinstance(value, str) and value for value in values)):
            raise ctx.fail(f"field `{self.key}` got {called(given)} as its options.",
                           "Give a list of texts, each once: `options=[\"open\", \"closed\"]`, or "
                           "`options={\"open\": \"Open\", \"closed\": \"Closed\"}` to word them.")
        return ([{"value": value, "label": value if label is None else say(label)} for value, label in pairs],
                tuple(values), "")

    def expect(self, ctx: Build, handler: Any) -> rules.Expect:
        """The field as the server checks it."""
        if self.searches:
            ident = self.searched_id(handler)
            other = rules.searched(ident, self.options, f"{runs.key_of(handler)}|{self.key}")
            if other:
                raise ctx.fail(f"field `{self.key}` of `{runs.key_of(handler)}` searches "
                               f"`{self.options.fn.__ui_key__}`; elsewhere it searches `{other}`.",
                               "A handler receives one set of fields: give this form or button a handler of its own.")
            return rules.Expect(self.key, "choice", bool(self.required), search=ident)
        fixed, field = self.choices(ctx, handler)[1:] if self.is_a in _CHOICES else (None, "")
        return rules.Expect(self.key, self.is_a, bool(self.required), fixed, field)

    def _start(self) -> Any:
        """The value the field starts with, as its input takes it."""
        value = self.value
        if self.is_a == "choices":
            return value.to(list[str]) if isinstance(value, rx.Var) else [str(one) for one in value or ()]
        if isinstance(value, rx.Var):
            return value if self.is_a == "bool" else value.to(str)
        if self.is_a == "bool":
            return bool(value)
        if value is None:
            return ""
        if isinstance(value, datetime.datetime):
            return value.strftime("%Y-%m-%dT%H:%M")
        if isinstance(value, datetime.date):
            return value.isoformat()
        return str(value)

    def control(self, ctx: Build, handler: Any, resets: list | None = None) -> rx.Component:
        """RFDS's input for the field's kind, with its label, its help and the server's message.

        ``resets`` collects the events that put the field back to its start value (a dialog opening again).
        """
        import rfds
        from rfds.components.inputs._shared import client_state

        from lex.lex_app.reflex.ui.state import State

        kind, start = self.is_a, self._start()
        common: dict[str, Any] = {
            "name": self.key, "label": self.words(), "required": bool(self.required),
            "hint": say(self.help) if self.help is not None else None,
            "error": State.ui_invalid.get(f"{runs.key_of(handler)}|{self.key}", ""),
        }
        if kind == "text" and self.lines is not None:
            # A text area is as wide as the form: a note or a pasted list needs the room.
            return rfds.textarea(default_value=start, rows=self.lines, width="full", **common)
        if kind == "text":
            return rfds.text_input(default_value=start, **common)
        if kind == "number":
            return rfds.number_input(default_value=start, digits=6, **common)
        if kind == "year":
            return rfds.text_input(default_value=start, input_mode="numeric", max_length=4, width="xs", **common)
        if kind == "datetime":
            # RFDS 1.0.0 has no input for a day with a time: the browser's own, in RFDS's field frame.
            return rfds.field(rx.input(type="datetime-local", name=self.key, default_value=start), **common)
        # The other inputs are RFDS's controlled ones. Their value is kept in the browser (a client
        # state) until the form is sent: nothing reaches the server while the user types.
        held = client_state("lexField", start)
        if resets is not None:
            resets.append(held.set_value(start))
        if kind == "money":
            from lex.lex_app.reflex.design import format_settings

            return rfds.money_input(value=held.value, on_change=held.set_value, allow_negative=True,
                                    currency=self.currency or format_settings()["base_currency"], **common)
        if kind == "percent":
            return rfds.percent_input(value=held.value, on_change=held.set_value, **common)
        if kind == "date":
            return rfds.date_input(value=held.value, on_change=held.set_value, width="sm", **common)
        if kind == "bool":
            return rfds.checkbox(checked=held.value, on_change=held.set_value, **common)
        if self.searches:
            return self._searched(handler, common, resets)
        options = self.choices(ctx, handler)[0]
        short = isinstance(options, list) and len(options) <= DROPDOWN_OPTIONS
        if kind == "choices":
            # RFDS: a checkbox group for several of a short list, its multi-select above 12 options.
            pick = rfds.checkbox_group if short else rfds.multi_select
            return pick(options=options, value=held.value, on_change=held.set_value, **common)
        if short:
            return rfds.select(options=options, value=held.value, on_change=held.set_value, **common)
        return rfds.combobox(options=options, value=held.value, on_change=held.set_value, **common)

    def _searched(self, handler: Any, common: dict, resets: list | None) -> rx.Component:
        """RFDS's combobox in its server-search mode, over the matches the server keeps for this field.

        The combobox shows and sends what it holds as its value. Here it holds the chosen match's
        label, under a name the server ignores; the id is held beside it and sent under the
        field's key. The first page of matches is asked for when the field appears.
        """
        import rfds
        from rfds.components.inputs._shared import client_state

        from lex.lex_app.reflex.ui.state import State

        ident = self.searched_id(handler)
        matches = State.ui_matches.get(ident, [])
        chosen, words = client_state("lexField", ""), client_state("lexWords", "")
        if resets is not None:
            resets.extend([chosen.set_value(""), words.set_value("")])

        def pick(value: rx.Var) -> list:
            label = rx.Var(_js_expr=f"((({matches}) || []).find((one) => one.value === {value})?.label ?? {value})",
                           _var_type=str, _var_data=rx.vars.VarData.merge(matches._get_all_var_data(),
                                                                          value._get_all_var_data()))
            return [chosen.set_value(value), words.set_value(label)]

        box = rfds.combobox(options=matches, on_search=lambda text: State.ui_search(ident, text), value=words.value,
                            on_change=pick, **{**common, "name": f"{self.key}__shown"})
        return rx.fragment(box, rx.el.input(type="hidden", name=self.key, value=chosen.value),
                           on_mount=State.ui_search(ident, ""))

    def _render(self, ctx: Build) -> rx.Component:
        raise ctx.fail(f"field `{self.key}` stands outside a form.",
                       "A field goes in `ui.form(fields=[...])`, a `ui.step(..., fields=[...])` or a button's `ask=[...]`.")


def field(key: str, *, label: Any = None, kind: str | None = None, options: Any = None, required: bool = False,
          help: Any = None, value: Any = None, currency: Any = None,  # noqa: A002 - the design's word
          lines: int | None = None) -> Field:
    """One input of a form. The handler finds what was entered under ``key``: ``values["name"]``.

    ``kind`` says what the value is, and with it what the handler receives:

    =============  ==========================================  ==========================
    ``text``       a line of text (the default), or several    ``str``, ``""`` when empty
                   with ``lines=``
    ``number``     a number                                    ``int``, or ``Decimal`` with decimals
    ``money``      an amount, in ``currency=`` (a code such    ``Decimal``
                   as ``"EUR"``; else the home currency)
    ``percent``    a percentage: 5.25 % arrives as 0.0525      ``Decimal``
    ``date``       a day                                       ``datetime.date``
    ``datetime``   a day and a time, in the app's time zone    ``datetime.datetime``
    ``year``       four digits                                 ``int``
    ``bool``       a box to tick                               ``bool``
    ``choice``     one of ``options=``                         ``str``
    ``choices``    several of ``options=``: boxes to tick,     ``list[str]``, in the order
                   or a list to pick from above 12 options     of the options
    =============  ==========================================  ==========================

    Nothing entered arrives as ``None`` (``""`` for text, ``False`` for a box, ``[]`` for
    several). ``options=`` is a list of texts, a dict of value to wording, or a field of the
    handler's own state; a field with options is a choice, and ``kind="choices"`` lets the user
    choose several of them.

    One among thousands, or among names that must not all reach the browser, is searched on the
    server: ``options=`` names a ``@ui.rows`` handler, whose rows give ``id`` (what the handler
    receives; a number stays a number) and ``label`` (the words the user reads)::

        @ui.rows
        def customers(self, search):
            return [{"id": c.id, "label": c.name} for c in find_customers(self.user, search)]

        ui.field("customer", options=OrdersState.customers, required=True)

    The user types; the rows handler runs as the user and the browser gets a page of matches,
    id and label only. A rows handler without ``search`` returns every row, and lex-app matches
    the typed words against the labels. The first page is there when the field opens. The value
    sent back must be one this browser tab was offered, else it is refused at the field. A
    searched choice starts empty (no ``value=``) and chooses one (not ``kind="choices"``). ``label`` defaults to the key with a capital; ``help`` is a sentence
    under it; ``value`` is what the field starts with (a value -- a list for several -- or a
    state value the page's load filled). ``required=True`` (for several: at least one) and the
    kind are checked before the handler runs, and the message is shown at the field.

    ``lines=6`` makes a text field several lines high, for notes, a comment or a pasted list:
    ``ui.field("notes", lines=6)``. The handler still gets a ``str``, with its line breaks, trimmed
    at the ends (``values["codes"].splitlines()`` for a list). It goes with text only.
    """
    if not isinstance(key, str) or not key.isidentifier() or key.startswith("_"):
        raise TypeError(f"ui.field() takes the key first, the name the handler finds the value under: "
                        f"`ui.field(\"name\")`; got {key!r}")
    if not isinstance(required, bool):
        raise TypeError(f"ui.field({key!r}, required=) is True or False; got {required!r}")
    if lines is not None and (not isinstance(lines, int) or isinstance(lines, bool) or lines < 2):
        raise TypeError(f"ui.field({key!r}, lines=) is how many lines the field shows, 2 or more; got {lines!r}. "
                        "A field of one line is the default: leave `lines=` out.")
    return Field(key, label, kind, options, required, help, value, currency, lines)


@dataclasses.dataclass(eq=False)
class Step:
    """One step of a wizard: a title and its fields."""

    title: Any
    fields: tuple = ()


def step(title: str, *, fields: Any) -> Step:
    """One step of a wizard: ``ui.form(steps=[ui.step("Details", fields=[...]), ...])``."""
    if not isinstance(title, str) or not title:
        raise TypeError(f"ui.step() takes its title first, a sentence; got {title!r}")
    return Step(title, kinds._many(fields))


def _fields(ctx: Build, given: Any, seen: dict | None = None) -> list:
    """The fields of one slot, each checked; a key may be used once in a form."""
    found = take(ctx, given, (Field,), f"`{ctx.slot.split('[')[0].split('.')[-1]}=` holds {_FIELD}.")
    seen = {} if seen is None else seen
    for one in found:
        one.check(ctx)
        if one.key in seen:
            raise ctx.fail(f"two fields have the key `{one.key}`.",
                           "The handler finds each value under its field's key: give each field its own.")
        seen[one.key] = one
    return found


def _receiver(ctx: Build, handler: Any, also: int = 0) -> Any:
    """``handler`` checked to be one that can receive values: an action or a job taking ``values`` last."""
    fn = getattr(handler, "fn", None)
    body = getattr(fn, "__ui_fn__", None)
    name = ".".join(body.__qualname__.split(".")[-2:]) if body is not None else ""
    if body is None or getattr(fn, "__ui_kind__", "") not in ("action", "job"):
        got = f"`{name}`, which is an `@ui.{fn.__ui_kind__}`" if body is not None else called(handler)
        raise ctx.fail(f"got {got}.", "The values go to a handler declared with `@ui.action` or `@ui.job`: "
                                      "`def create(self, values): ...`.")
    names = list(inspect.signature(body).parameters)[1:]
    if len(names) != also + 1 or names[-1] != rules.VALUES:
        before = "".join(f", {one}" for one in names[:also]) if len(names) >= also else ", key" * also
        raise ctx.fail(f"`{name}` takes ({', '.join(['self', *names])}).",
                       f"It receives what was entered as its last argument, called `values`: "
                       f"`def {body.__name__}(self{before}, values): ...`; `values[\"name\"]` is the field "
                       f"`ui.field(\"name\")`.")
    return handler


def _register(ctx: Build, handler: Any, receives: rules.Receives) -> None:
    try:
        rules.register(handler, receives)
    except ValueError as wrong:
        raise ctx.fail(str(wrong).split(". ")[0] + ".", "A handler receives one set of fields: give this one a "
                                                        "handler of its own.") from None


def _refusal(handler: Any) -> rx.Component:
    """The form's last refusal, above its submit button: RFDS's inline message, headed by its title."""
    import rfds

    from lex.lex_app.reflex.ui.state import State

    refusal = State.ui_refused.get(runs.key_of(handler), {})
    title, message = refusal.get("title", "").to(str), refusal.get("message", "").to(str)
    # A warning, as the toast it replaces; "alert": it is new, and the form did not do what was asked.
    return rx.cond(message != "", rx.cond(
        title != "", rfds.inline_message(message, title=title, tone="warning", role="alert"),
        rfds.inline_message(message, tone="warning", role="alert")))


def _progress(handler: Any) -> rx.Component:
    """A job's progress while it runs, with a way to stop it."""
    import rfds

    from lex.lex_app.reflex.ui.state import State

    key = runs.key_of(handler)
    fraction = State.ui_progress.get(key, 0.0)
    message = State.ui_progress_message.get(key, "")
    percent = rx.Var(_js_expr=f"Math.round(({fraction!s}) * 100)", _var_type=int,
                     _var_data=fraction._get_all_var_data())
    bar = rfds.progress_bar(percent, label=rx.cond(message != "", message, say("Working")), indeterminate=fraction <= 0)
    return rx.cond(runs.running(handler), rfds.stack(bar, rfds.button(say("Stop"), on_click=runs.cancel(handler))))


@dataclasses.dataclass(eq=False)
class Form(kinds.Kind):
    title: Any
    load: Any = None
    fields: tuple = ()
    steps: tuple = ()
    submit: Any = None
    result: tuple = ()
    actions: tuple = ()
    tabs: bool = False
    keep: bool = False

    def loads(self) -> tuple[list, list]:
        parts = [part for part in self.result if isinstance(part, kinds.Section)]
        return ([self.load] if self.load is not None else []), [part.load for part in parts if part.load is not None]

    def _steps(self, ctx: Build) -> list:
        """Per step, its title and its checked fields; a form without steps is one untitled step."""
        if bool(self.fields) == bool(self.steps):
            raise ctx.at("fields").fail("a form has `fields=` or `steps=`, one of them." if self.fields
                                        else "the form has no fields.",
                                        "Write `fields=[ui.field(\"name\"), ...]`, or for a wizard "
                                        "`steps=[ui.step(\"Details\", fields=[...]), ...]`.")
        if self.fields:
            return [(None, _fields(ctx.at("fields"), list(self.fields)))]
        given = take(ctx.at("steps"), list(self.steps), (Step,), "`steps=` holds `ui.step(title, fields=[...])`.")
        if len(given) < 2:
            raise ctx.at("steps").fail("a wizard with one step.", "One step is a form: write `fields=[...]`.")
        seen: dict = {}
        found = []
        for index, one in enumerate(given):
            fields = _fields(ctx.at(f"steps[{index}].fields"), list(one.fields), seen)
            if not fields:
                raise ctx.at(f"steps[{index}]").fail(f"the step `{one.title}` has no fields.",
                                                     "Give it `fields=[ui.field(...)]`, or leave the step out.")
            found.append((one.title, fields))
        return found

    def _result(self, ctx: Build) -> list:
        """What the page shows after a successful submit, built."""
        import rfds

        parts = take(ctx, list(self.result), (*vocab.CONTENT, tables.Table, kinds.Section),
                     "`result=` holds what a section holds -- `ui.text`, `ui.table`, `ui.button`... -- or "
                     "`ui.section(title, ...)`s.")
        if self.tabs:
            return self._tabs(ctx, parts)
        built: list = []
        loose: list = []
        for index, part in enumerate(parts):
            if not isinstance(part, kinds.Section):
                loose.append(part)
                continue
            built.extend(kinds._stacked(ctx, loose))
            loose = []
            at = ctx.at(f"result[{index}]")
            built.append(shown(at, part.titled(at), part.visible))
        built.extend(kinds._stacked(ctx, loose))
        return built

    def _tabs(self, ctx: Build, parts: list) -> list:
        """The result with ``tabs=True``: what every tab shares, then RFDS's tabs, one per section."""
        import rfds
        from rfds.components.inputs._shared import client_state
        from rfds.components.navigation import TabSpec

        from lex.lex_app.reflex.access import permission
        from lex.lex_app.reflex.ui import guard

        sections = [part for part in parts if isinstance(part, kinds.Section)]
        if len(sections) < 2:
            raise ctx.fail(f"`tabs=True` shows the result's sections one at a time, and it has {len(sections)}.",
                           "Give `result=` two or more `ui.section(title, ...)`s, one per tab, or leave `tabs=` out.")
        first = parts.index(sections[0])
        after = [part for part in parts[first:] if not isinstance(part, kinds.Section)]
        if after:
            raise ctx.fail(f"{called(after[0])} comes after a section, and with `tabs=True` the sections are the tabs.",
                           "Put what every tab shares first, then the sections.")
        titles = [part.title for part in sections]
        twice = sorted({title for title in titles if titles.count(title) > 1})
        if twice:
            raise ctx.fail(f"two sections are both called `{twice[0]}`.", "Give each tab's section its own title.")
        specs: list = []
        for index, (part, ident) in enumerate(zip(sections, kinds._section_ids(titles)), start=first):
            at = ctx.at(f"result[{index}]")
            if part.collapsed:
                raise at.fail(f"the section `{part.title}` is collapsed, and a tab is opened by its tab.",
                              "Leave `collapsed=` out.")
            needs = None
            if isinstance(part.visible, guard.Requirement):
                needs = permission(part.visible.target, part.visible.scope)     # RFDS's tab permission
            elif not (part.visible is None or part.visible is guard.signed_in):
                raise at.fail("`visible=` on a tab takes a permission.",
                              "A tab always shows. To hide it from some users write `visible=ui.read(Model)`; to "
                              "show different content by state, put `ui.switch(...)` inside the section.")
            specs.append(TabSpec(value=ident, label_id=part.title, label_default=part.title,
                                 content=rx.el.div(part.body(at), id=ident), permission=needs))
        current = client_state("lexTab", specs[0].value)
        tabs = kinds._rfds(ctx, lambda: rfds.tabs(*specs, value=current.value, on_change=current.set,
                                                  label=say("Result")))
        return [*kinds._stacked(ctx, parts[:first]), tabs]

    def _page(self, ctx: Build) -> rx.Component:
        import rfds
        from rfds.components.inputs import form_actions, form_scope, required_legend
        from rfds.components.navigation import StepSpec, stepper

        from lex.lex_app.reflex.ui.state import State

        if self.submit is None:
            raise ctx.at("submit").fail("the form has no `submit=`.",
                                        "Name the handler that receives the values: `submit=OrdersState.create`.")
        if isinstance(self.submit, vocab.Button):
            raise ctx.at("submit").fail("`submit=` got a `ui.button`.",
                                        "Name the handler itself: `submit=OrdersState.create`. The button is "
                                        "called after it (\"Create\").")
        handler = _receiver(ctx.at("submit"), self.submit)
        key = runs.key_of(handler)
        title, actions, steps = self._title(ctx), self._buttons(ctx, lead=False), self._steps(ctx)
        every = [one for _, fields in steps for one in fields]
        wizard = len(steps) > 1
        _register(ctx.at("submit"), handler, rules.Receives(
            fields=tuple(one.expect(ctx.at("fields"), handler) for one in every),
            steps=tuple(tuple(one.key for one in fields) for _, fields in steps) if wizard else (),
            where=f"page `{ctx.page}`"))
        if self.tabs and not self.result:
            raise ctx.at("result").fail("`tabs=True` shows the result's sections one at a time, and it has 0.",
                                        "Give `result=` two or more `ui.section(title, ...)`s, one per tab, or leave "
                                        "`tabs=` out.")
        if self.keep:
            rules.keep(handler)
        if self.load is not None:
            ctx = dataclasses.replace(ctx, under_load=True)

        form_id = slug(key)
        at = State.ui_step.get(key, 0)
        last = len(steps) - 1
        body: list = []
        if any(one.required for one in every):
            body.append(required_legend())
        with form_scope(form_id):
            groups = [[one.control(ctx.at("fields"), handler) for one in fields] for _, fields in steps]
        if wizard:
            # Every step's fields stay in the one form, the other steps hidden: it is sent whole, once.
            body.insert(0, kinds._rfds(ctx.at("steps"), lambda: stepper(
                [StepSpec(id=f"step-{index + 1}", label_id=text, label_default=text)
                 for index, (text, _) in enumerate(steps)], current=at, label=say("Steps"))))
            body.extend(rx.el.div(rfds.stack(*group), hidden=at != index) for index, group in enumerate(groups))
        else:
            body.extend(groups[0])
        name = say(handler.fn.__name__.replace("_", " ").capitalize())
        send = rfds.button(rx.cond(at < last, say("Continue"), name) if wizard else name, type="submit",
                           intent="primary", loading=runs.running(handler))
        buttons = [send]
        if wizard:
            buttons.append(rx.cond(at > 0, rfds.button(say("Back"), intent="tertiary", on_click=State.ui_step_back(key))))
        form = rfds.form(*body, _refusal(handler), form_actions(*buttons), id=form_id, on_submit=handler)
        entry: list = [form]
        if handler.fn.__ui_kind__ == "job":
            entry.append(_progress(handler))
        if self.keep:       # the user may have been away when its failure was toasted, or its result went
            # A refusal is already above the submit button, with its title (and kept across visits): not twice.
            problem = runs.failed(handler)
            refused = State.ui_refused.get(key, {}).get("message", "").to(str)
            entry.append(rx.cond((problem != "") & (refused == ""), rfds.inline_message(problem, tone="warning")))
        header = kinds._rfds(ctx, lambda: rfds.page_header(title=title, path=ctx.route or "/",
                                                            secondary_actions=actions))
        whole: list = [rfds.page(header, *entry, width="form")]            # a form is narrow
        if self.result:
            after = dataclasses.replace(ctx.at("result"), under_load=True,
                                        caption=self.title if isinstance(self.title, str) else "")
            # Its result takes the page's width: it is where the tables are.
            whole.append(rx.cond(State.ui_done.get(key, False), rfds.page(rfds.stack(*self._result(after)))))
        return kinds.states(ctx, self.load, rx.fragment(*whole), "rect")


def form(*, title: Any, load: Any = None, fields: Any = (), steps: Any = (), submit: Any = None,
         result: Any = (), actions: Any = (), tabs: bool = False, keep: bool = False) -> Form:
    """Entering something, at once or in steps. A tool is a form with a ``result=``.

    ``fields=`` is a list of ``ui.field``; ``steps=[ui.step("Details", fields=[...]), ...]`` makes
    it a wizard instead. ``submit=`` names the handler -- an ``@ui.action``, or an ``@ui.job`` for
    long work -- that receives what was entered, once, as its ``values`` argument::

        @ui.action
        def create(self, values):
            orders.create(self.user.id, values["name"], values["amount"])
            return ui.goto(ui.address(order_list))

    Before the body runs lex-app checks every field (``required=``, and that the value is of the
    field's kind); a problem is shown at its field and the body does not run. The submit button is
    called after the handler (``create`` is "Create"), shows it running, and for a job its progress
    with a way to stop it. A refusal (``ui.Refuse``) is shown in the form, above the submit button,
    until the form is sent again; an unexpected failure is a toast, as for every handler.

    ``result=`` is what the page shows once the handler has succeeded, and until the form is sent
    again: what a section holds, or ``ui.section``\\ s. The handler keeps what the result shows in
    its state -- large or private data in a field whose name starts with ``_``, which stays on the
    server -- and a ``@ui.rows`` handler or a computed value reads it. ``actions`` are placed in
    the page header, none of them as the primary one: that is the submit button. ``tabs=True``
    shows the result's sections one at a time, as on a record page: each section's title is its
    tab, and what comes before the first section is above the tabs.

    A new visit starts the form over: its result leaves. ``keep=True`` keeps the result when the
    user leaves the page and comes back -- a job that finished meanwhile shows what it found, one
    still running its progress -- for as long as what the result shows is kept: a ``ui.private``
    field needs ``keep=True`` too, and a field the page's ``load=`` resets needs that load's
    ``keep=``. A kept form also keeps its refusal above its submit button, shows its handler's
    last other failure under it, and says so when its result is no longer kept. What was typed is
    not kept by lex-app (it may be personal): to show it again, the handler sets a field and the
    input starts from it (a searched choice starts empty, also on a kept form)::

        @ui.job
        def build(self, values):
            self.customer = values["customer"]          # ui.field("customer", value=ReportState.customer)
            self._lines = invoices_of(self.user.id, values["customer"])
    """
    if not isinstance(tabs, bool):
        raise TypeError(f"ui.form(tabs=) takes True or False; got {tabs!r}. `tabs=True` shows the result's sections "
                        "one at a time.")
    if not isinstance(keep, bool):
        raise TypeError(f"ui.form(keep=) is True or False; got {keep!r}")
    return Form(title, load, kinds._many(fields), kinds._many(steps), submit, kinds._many(result), kinds._many(actions),
                tabs, keep)


def dialog(ctx: Build, button: Any) -> Any:
    """The dialog of a button with ``confirm=`` or ``ask=``; returns the event that opens it.

    One dialog per button as written, also for a button on every row of a table: the row's values
    are kept with the open dialog and passed on when it is answered.
    """
    import rfds
    from rfds.components.inputs import form_actions, form_scope

    from lex.lex_app.reflex.ui.state import State

    handler, question = button.handler, button.confirm
    written = (rules.ident(handler), str(button.label), str(question), button.tone,
               tuple(one.key for one in button.ask if isinstance(one, Field)), tuple(str(one) for one in button.args))
    if written in ctx.asked:
        return ctx.asked[written]
    ident = f"{slug(ctx.page)}-ask-{next(_DIALOGS)}"
    is_open = State.ui_asking == ident
    passed = [State.ui_asking_args[index] for index in range(len(button.args))]
    opening: list = []
    if question is not None:
        if isinstance(question, str) and (not question.rstrip().endswith("?") or "are you sure" in question.lower()):
            raise ctx.fail(f"`confirm=` is `{question}`.",
                           "Ask what is about to happen, naming the action and its object, as a question: "
                           "`confirm=\"Close this order?\"`. The button to agree repeats the button's label.")
        question = say(question)
    if button.ask:
        _receiver(ctx, handler, also=len(passed))
        asked = _fields(ctx.at(f"{ctx.slot}.ask" if ctx.slot else "ask"), list(button.ask))
        _register(ctx, handler, rules.Receives(fields=tuple(one.expect(ctx, handler) for one in asked),
                                               where=f"the button `{button.label}` on page `{ctx.page}`", dialog=True))
        with form_scope(ident):
            controls = [one.control(ctx, handler, opening) for one in asked]
        send = rfds.button(say(button.label), type="submit", loading=runs.running(handler),
                           intent="danger" if button.tone == "danger" else "primary")
        body = rfds.form(*controls, _refusal(handler),
                         form_actions(send, rfds.button(say("Cancel"), on_click=State.ui_ask_close(False))),
                         id=ident, on_submit=lambda data: handler(*passed, data))
        shown_dialog = kinds._rfds(ctx, lambda: rfds.dialog(body, open=is_open, on_open_change=State.ui_ask_close,
                                                            title=say(button.label), description=question))
    else:
        shown_dialog = kinds._rfds(ctx, lambda: rfds.confirm_dialog(
            open=is_open, on_open_change=State.ui_ask_close, title=question, body=rx.fragment(),
            confirm_label=say(button.label), on_confirm=[State.ui_ask_close(False), handler(*passed)],
            tone="danger" if button.tone == "danger" else "neutral"))
    ctx.overlays.append(shown_dialog)
    ctx.asked[written] = [*opening, State.ui_ask(ident, list(button.args))]
    return ctx.asked[written]
